"""Turning received voice packets into PCM.

Shared by /record and /call: both need Discord's end-to-end encryption undone
before the Opus decoder will accept a packet.
"""
import audioop
import io
import logging

from pydub import AudioSegment, effects
from pydub.utils import which

from discord.opus import OPUS_SILENCE, Decoder as OpusDecoder

try:
    import davey
except Exception:  # pragma: no cover - optional, ships with discord.py[voice]
    davey = None

_log = logging.getLogger(__name__)


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


def drain_socket(voice_client, limit: int = 65536) -> int:
    """Throw away voice packets that queued up in the OS socket buffer.

    discord.py's SocketReader only reads the voice socket while something is
    registered as a listener. Between recordings nothing is, so incoming packets
    sit in the kernel receive buffer — and the next listen() drains the whole
    backlog at once, each packet still carrying its original RTP timestamp. That
    makes a fresh recording start minutes in the past.

    Returns how many packets were discarded.
    """
    connection = getattr(voice_client, '_connection', None)
    sock = getattr(connection, 'socket', None)
    # Only safe on the non-blocking socket discord.py creates; if it's anything
    # else, recv could block the command forever.
    if sock is None or sock.gettimeout() != 0:
        return 0

    discarded = 0
    while discarded < limit:
        try:
            sock.recv(65535)
        except (BlockingIOError, InterruptedError, OSError):
            break  # nothing buffered: caught up with live traffic
        discarded += 1
    return discarded


# --- shared audio helpers ----------------------------------------------------
# Discord decodes voice to 48kHz 16-bit stereo, but each user's Opus stream is
# mono, so the two channels are identical. Captures store mono: same audio,
# half the memory, and a smaller upload.
SAMPLE_RATE = 48000
SAMPLE_WIDTH = 2
MONO_FRAME_WIDTH = SAMPLE_WIDTH


def to_mono(raw: bytes) -> bytes:
    """Fold a dual-mono stereo frame down. audioop needs whole stereo frames,
    so any ragged tail is dropped."""
    stereo_width = SAMPLE_WIDTH * 2
    pcm = raw[:len(raw) // stereo_width * stereo_width]
    return audioop.tomono(pcm, SAMPLE_WIDTH, 0.5, 0.5) if pcm else b''


def mono_segment(raw: bytes) -> AudioSegment:
    return AudioSegment(bytes(raw), metadata={'channels': 1,
                                              'sample_width': SAMPLE_WIDTH,
                                              'frame_rate': SAMPLE_RATE,
                                              'frame_width': MONO_FRAME_WIDTH})


def mix_tracks(tracks: dict) -> AudioSegment | None:
    """Overlay per-speaker segments that already share a zero point."""
    segments = list(tracks.values())
    if not segments:
        return None
    if len(segments) == 1:
        return effects.normalize(segments[0], headroom=1.0)

    # overlay() truncates to the base, so the bed has to be the full length.
    # Summing saturates, so give each speaker room before mixing and take the
    # level back with a normalize afterwards.
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
