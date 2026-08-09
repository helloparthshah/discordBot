import asyncio
import io
import logging
import threading
import time

import discord
from discord import Embed, app_commands
from discord.ext import commands
from pydub import AudioSegment, effects

from utils import clip_buffer
from utils.audio_player import init_voice_client
from utils.voice_client import MISSING_DEPENDENCY_MESSAGE, RECV_AVAILABLE, voice_recv
from utils.voice_receive import (MONO_FRAME_WIDTH as TRACK_FRAME_WIDTH,
                                 SAMPLE_RATE, SAMPLE_WIDTH, PacketDecoder,
                                 drain_socket, encode, mix_tracks,
                                 mono_segment as _segment, sanitize, to_mono)

_log = logging.getLogger(__name__)

MAX_SECONDS = 300
MAX_ATTACHMENTS = 10


class ChannelRecorder:
    """Collects each speaker's audio onto one shared timeline.

    Discord sends nothing at all while someone is silent, so a user's packets
    can't simply be concatenated — that collapses their pauses and drifts them
    out of sync with everyone else. Each speaker is anchored by wall clock at
    their first packet and positioned by RTP timestamp from then on, which is
    sample-exact and doesn't care what order packets arrive in.
    """

    def __init__(self, decoder: PacketDecoder | None = None,
                 limit_ms: int | None = None):
        self.decoder = decoder
        # Hard ceiling on the timeline. Anything landing past the requested
        # window is dropped, so a recording can never run longer than it was
        # asked for no matter what timestamps show up.
        self.limit = (int(limit_ms / 1000 * SAMPLE_RATE) * TRACK_FRAME_WIDTH
                      if limit_ms else None)
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

        pcm = to_mono(raw)
        if not pcm:
            return
        timestamp = getattr(data.packet, 'timestamp', None)

        with self._lock:
            track = self.tracks.get(user.id)
            if track is None:
                track = self.tracks[user.id] = bytearray()
                self.speakers[user.id] = getattr(user, 'display_name', str(user.id))
                lead_in = int((time.perf_counter() - self.started) * SAMPLE_RATE)
                self._anchors[user.id] = (timestamp, max(0, lead_in) * TRACK_FRAME_WIDTH)

            offset = self._offset_for(user.id, timestamp, len(track))
            if self.limit is not None:
                if offset >= self.limit:
                    return  # lands outside the requested window
                pcm = pcm[:self.limit - offset]

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
        return mix_tracks(self.snapshot())


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

        # Claimed before any await: two /records racing through this check would
        # both call listen(), and the loser's error handling would stop the
        # winner's reader.
        if inter.guild.id in self.active:
            return await inter.followup.send("Already recording in this server.")
        self.active.add(inter.guild.id)
        try:
            await self._record(inter, seconds, separate)
        finally:
            self.active.discard(inter.guild.id)

    async def _record(self, inter: discord.Interaction, seconds: int, separate: bool):
        if not await init_voice_client(inter):
            return

        vc = inter.guild.voice_client
        if not isinstance(vc, voice_recv.VoiceRecvClient):
            # Only reachable if I joined before recording support was loaded —
            # every new connection uses the recording-capable client.
            return await inter.followup.send(
                "I joined this channel with a voice client that can't record. "
                "Disconnect me from the voice channel and run /record again.")
        # One receiver per guild. This also releases the rolling clip buffer,
        # which holds it while the bot is idle; the clip cog's maintenance loop
        # starts it again once we're done.
        if not clip_buffer.claim_receiver(inter.guild):
            return await inter.followup.send(
                "I'm already receiving audio here (a recording or a call).")

        decoder = PacketDecoder(vc)
        recorder = ChannelRecorder(decoder, limit_ms=seconds * 1000)
        try:
            # Nothing has been reading the socket since the last recording, so
            # clear the backlog before listening or it all arrives as "now".
            stale = drain_socket(vc)
            if stale:
                _log.debug("Discarded %d packets buffered since the last listen", stale)
            recorder.started = time.perf_counter()

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
