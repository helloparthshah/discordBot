import asyncio
import audioop
import io
import logging
import threading
import time

import discord
from discord import Embed, app_commands
from discord.ext import commands
from discord.opus import OPUS_SILENCE, Decoder as OpusDecoder
from pydub import AudioSegment, effects
from pydub.utils import which

from utils.audio_player import init_voice_client
from utils.voice_client import MISSING_DEPENDENCY_MESSAGE, RECV_AVAILABLE, voice_recv

try:
    import davey
except Exception:  # pragma: no cover - optional, ships with discord.py[voice]
    davey = None

_log = logging.getLogger(__name__)

# Discord decodes voice to 48kHz 16-bit stereo, but each user's Opus stream is
# mono, so the two channels are identical. Tracks are stored mono: same audio,
# half the memory, and a smaller upload.
SAMPLE_RATE = 48000
SAMPLE_WIDTH = 2
TRACK_FRAME_WIDTH = SAMPLE_WIDTH

MAX_SECONDS = 300
MAX_ATTACHMENTS = 10


def _segment(raw: bytes) -> AudioSegment:
    return AudioSegment(bytes(raw), metadata={'channels': 1,
                                              'sample_width': SAMPLE_WIDTH,
                                              'frame_rate': SAMPLE_RATE,
                                              'frame_width': TRACK_FRAME_WIDTH})


class PacketDecoder:
    """Turns received packets into PCM, handling Discord's E2EE layer.

    voice_recv decodes Opus itself, but only ever does *transport* decryption —
    it has no knowledge of DAVE, Discord's end-to-end encryption. discord.py
    advertises DAVE whenever the `davey` library is installed, so on a normal
    call what voice_recv feeds to the Opus decoder is still ciphertext and it
    dies with `OpusError: corrupted stream`.

    So we take packets undecoded (`decode=False`) and do both steps here: E2EE
    decrypt through the MLS session the bot is already a member of, then decode.
    The alternative — refusing DAVE so the audio arrives in the clear — would
    silently drop end-to-end encryption for everyone else on the call.
    """

    def __init__(self, voice_client):
        self.voice_client = voice_client
        self._decoders: dict[int, OpusDecoder] = {}
        self.decoded = 0
        self.dropped = 0
        self._last_error: Exception | None = None

    @property
    def _session(self):
        state = getattr(self.voice_client, '_connection', None)
        return getattr(state, 'dave_session', None) if state is not None else None

    def to_pcm(self, user_id: int, data) -> bytes | None:
        # Loss-concealment placeholders carry no real audio — they're either
        # empty or the Opus silence sentinel, which isn't an encrypted frame and
        # would be counted as a decrypt failure. The recorder's timeline already
        # leaves a hole where they'd go.
        payload = getattr(data.packet, 'decrypted_data', None)
        if not payload or payload == OPUS_SILENCE:
            return None

        session = self._session
        if session is not None and davey is not None:
            try:
                payload = session.decrypt(user_id, davey.MediaType.audio, payload)
            except Exception as exc:
                # Expected at the very start: the MLS group key exchange finishes
                # a beat after we start listening, so a speaker's cryptor may not
                # be registered yet ("NoValidCryptorFound"). Those packets are
                # dropped and the timeline leaves a hole. Counted, not logged
                # per packet — one traceback per recording reads like a crash.
                self.dropped += 1
                self._last_error = exc
                _log.debug("E2EE decrypt failed for %s: %s", user_id, exc)
                return None
            if not payload:
                self.dropped += 1
                return None

        decoder = self._decoders.get(user_id)
        if decoder is None:
            # Opus decoding is stateful, so each speaker needs their own
            decoder = self._decoders[user_id] = OpusDecoder()
        pcm = decoder.decode(payload, fec=False)
        self.decoded += 1
        return pcm

    def report(self) -> str | None:
        """One line about dropped packets, or None if it was a clean run."""
        if not self.dropped:
            return None
        total = self.decoded + self.dropped
        _log.info("Dropped %d/%d packets that couldn't be decrypted (%s)",
                  self.dropped, total, self._last_error)
        # A handful at startup is routine; a large share means something's wrong.
        if self.decoded and self.dropped / total < 0.05:
            return None
        return (f"{self.dropped} of {total} packets couldn't be decrypted "
                f"and are missing from this recording.")


class ChannelRecorder:
    """Collects each speaker's audio onto one shared timeline.

    Discord sends nothing at all while someone is silent, so a user's packets
    can't simply be concatenated — that collapses their pauses and drifts them
    out of sync with everyone else. Each speaker is anchored by wall clock at
    their first packet and positioned by RTP timestamp from then on, which is
    sample-exact and doesn't care what order packets arrive in.
    """

    def __init__(self, decoder: PacketDecoder | None = None):
        self.decoder = decoder
        self.started = time.perf_counter()
        self.tracks: dict[int, bytearray] = {}
        self.speakers: dict[int, str] = {}
        self._anchors: dict[int, tuple[int | None, int]] = {}
        self._lock = threading.Lock()

    def write(self, user, data) -> None:
        """Sink callback. Runs on the extension's reader thread, so it stays
        cheap: bytearray writes only, no decoding or mixing. It also swallows
        its own errors — raising here would take the reader down mid-recording.
        """
        try:
            self._write(user, data)
        except Exception:
            _log.exception("Dropped a voice packet while recording")

    def _write(self, user, data) -> None:
        if user is None:
            return

        raw = data.pcm or (self.decoder.to_pcm(user.id, data) if self.decoder else None)
        if not raw:
            return

        # Each stream is dual-mono; fold it down on the way in. audioop needs
        # whole stereo frames, so drop any ragged tail.
        stereo_width = SAMPLE_WIDTH * 2
        pcm = raw[:len(raw) // stereo_width * stereo_width]
        if not pcm:
            return
        pcm = audioop.tomono(pcm, SAMPLE_WIDTH, 0.5, 0.5)
        timestamp = getattr(data.packet, 'timestamp', None)

        with self._lock:
            track = self.tracks.get(user.id)
            if track is None:
                track = self.tracks[user.id] = bytearray()
                self.speakers[user.id] = getattr(user, 'display_name', str(user.id))
                lead_in = int((time.perf_counter() - self.started) * SAMPLE_RATE)
                self._anchors[user.id] = (timestamp, max(0, lead_in) * TRACK_FRAME_WIDTH)

            offset = self._offset_for(user.id, timestamp, len(track))
            if offset > len(track):
                track += b'\x00' * (offset - len(track))
            end = offset + len(pcm)
            if end > len(track):
                track += b'\x00' * (end - len(track))
            track[offset:end] = pcm

    def _offset_for(self, user_id: int, timestamp, fallback: int) -> int:
        base_timestamp, base_offset = self._anchors[user_id]
        if timestamp is None or base_timestamp is None:
            return fallback

        # RTP timestamps count samples and wrap at 2^32
        delta = (timestamp - base_timestamp) % (1 << 32)
        if delta > (1 << 31):
            # predates the anchor; treat as the very start of their track
            delta = 0
        return base_offset + delta * TRACK_FRAME_WIDTH

    def snapshot(self) -> dict[str, AudioSegment]:
        """Per-speaker segments, all sharing a zero point."""
        with self._lock:
            raw = {self.speakers[uid]: bytes(track)
                   for uid, track in self.tracks.items() if track}
        return {name: _segment(data) for name, data in raw.items()}

    def mix(self) -> AudioSegment | None:
        tracks = self.snapshot()
        if not tracks:
            return None

        segments = list(tracks.values())
        if len(segments) == 1:
            return effects.normalize(segments[0], headroom=1.0)

        # overlay() truncates to the base, so the bed has to be the full length.
        # Summing saturates, so give each speaker room before mixing and take
        # the level back with a normalize afterwards.
        longest = max(len(seg) for seg in segments)
        mixed = AudioSegment.silent(duration=longest, frame_rate=SAMPLE_RATE)
        for seg in segments:
            mixed = mixed.overlay(seg - 3.0)
        return effects.normalize(mixed, headroom=1.0)


def encode(segment: AudioSegment, basename: str) -> tuple[io.BytesIO, str]:
    """MP3 when ffmpeg is around, otherwise WAV — pydub writes wav itself."""
    buffer = io.BytesIO()
    if which("ffmpeg") or which("avconv"):
        segment.export(buffer, format="mp3", bitrate="128k")
        suffix = "mp3"
    else:
        segment.export(buffer, format="wav")
        suffix = "wav"
    buffer.seek(0)
    return buffer, f"{basename}.{suffix}"


def sanitize(name: str) -> str:
    keep = [c if c.isalnum() or c in "-_" else "-" for c in name]
    return "".join(keep).strip("-") or "speaker"


class Recording(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active: set[int] = set()

    @app_commands.command(name="record",
                          description="Record the voice channel and post the audio here")
    @app_commands.describe(
        seconds=f"How long to record, in seconds (max {MAX_SECONDS})",
        separate="Post one file per speaker instead of a single mixed file",
    )
    async def record(self, inter: discord.Interaction,
                     seconds: app_commands.Range[int, 1, MAX_SECONDS],
                     separate: bool = False):
        await inter.response.defer()

        if not RECV_AVAILABLE:
            return await inter.followup.send(MISSING_DEPENDENCY_MESSAGE)

        if inter.guild.id in self.active:
            return await inter.followup.send("Already recording in this server.")

        if not await init_voice_client(inter):
            return

        vc = inter.guild.voice_client
        if not isinstance(vc, voice_recv.VoiceRecvClient):
            # Only reachable if I joined before recording support was loaded —
            # every new connection uses the recording-capable client.
            return await inter.followup.send(
                "I joined this channel with a voice client that can't record. "
                "Disconnect me from the voice channel and run /record again.")
        if vc.is_listening():
            return await inter.followup.send("Already recording in this server.")

        decoder = PacketDecoder(vc)
        recorder = ChannelRecorder(decoder)
        self.active.add(inter.guild.id)
        try:
            # decode=False: we decrypt E2EE and decode Opus ourselves
            vc.listen(voice_recv.BasicSink(recorder.write, decode=False))

            # Everyone in the channel should know this is happening.
            await inter.followup.send(embed=self._notice(inter, seconds, vc.channel))
            await asyncio.sleep(seconds)
        except Exception as exc:
            _log.exception("Recording failed")
            return await inter.followup.send(f"Couldn't record: {exc}")
        finally:
            vc.stop_listening()
            self.active.discard(inter.guild.id)

        # let the last packets land before reading the buffers
        await asyncio.sleep(0.5)

        try:
            files, dropped = await asyncio.to_thread(self._build_files, recorder, separate)
        except Exception as exc:
            _log.exception("Failed to build recording")
            return await inter.followup.send(f"Couldn't put the recording together: {exc}")

        if not files:
            return await inter.followup.send("Nobody spoke, so there's nothing to post.")

        limit = inter.guild.filesize_limit
        total = sum(buf.getbuffer().nbytes for _, buf in files)
        if total > limit or any(buf.getbuffer().nbytes > limit for _, buf in files):
            return await inter.followup.send(
                f"The recording came out to {total // (1024 * 1024)}MB, over this "
                f"server's {limit // (1024 * 1024)}MB upload limit. Try a shorter "
                f"duration" + (" or drop `separate`." if separate else "."))

        note = f"Recording from {vc.channel.mention} — {seconds}s"
        if dropped:
            note += f" (only the first {MAX_ATTACHMENTS} speakers; {dropped} more omitted)"
        lost = decoder.report()
        if lost:
            note += f"\n-# {lost}"
        await inter.followup.send(
            content=note,
            files=[discord.File(buf, filename=name) for name, buf in files])

    def _notice(self, inter: discord.Interaction, seconds: int,
                channel: discord.abc.GuildChannel) -> Embed:
        embed = Embed(title="Recording", colour=0xed4245,
                      description=f"Recording {channel.mention} for **{seconds}s**.\n"
                                  f"Everyone in the channel is being captured.")
        embed.set_footer(text=f"Started by {inter.user.display_name}",
                         icon_url=inter.user.display_avatar.url)
        return embed

    def _build_files(self, recorder: ChannelRecorder,
                     separate: bool) -> tuple[list[tuple[str, io.BytesIO]], int]:
        """Runs in a worker thread — encoding walks the whole recording.

        Returns the files plus how many speakers were left out of a `separate`
        run, since Discord caps a message at 10 attachments.
        """
        if separate:
            tracks = sorted(recorder.snapshot().items())
            files = []
            for name, segment in tracks[:MAX_ATTACHMENTS]:
                buffer, filename = encode(effects.normalize(segment, headroom=1.0),
                                          f"recording-{sanitize(name)}")
                files.append((filename, buffer))
            return files, max(0, len(tracks) - MAX_ATTACHMENTS)

        mixed = recorder.mix()
        if mixed is None:
            return [], 0
        buffer, filename = encode(mixed, "recording")
        return [(filename, buffer)], 0


async def setup(bot):
    print("Adding Recording")
    # voice_recv logs an RTCP sender report per speaker per second, and the
    # voice-gateway fields it doesn't model, all at INFO. Both are normal on a
    # healthy connection and drown the log. Drop these two loggers to WARNING —
    # remove these lines to get the chatter back while debugging voice.
    for name in ("discord.ext.voice_recv.reader", "discord.ext.voice_recv.gateway"):
        logging.getLogger(name).setLevel(logging.WARNING)
    await bot.add_cog(Recording(bot))


async def teardown(bot):
    print("Unloaded Recording")
