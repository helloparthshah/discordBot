"""A rolling buffer of recent voice, so /clip can capture what already happened.

/record needs foresight; the funny thing has usually already been said. This
keeps the last minute of each speaker in memory so it can be grabbed after the
fact.

A guild has exactly one voice receiver, so this holds it while the bot is idle
in a channel and gives it up to /record and /call on demand — they call stop()
before claiming it, and the clip cog's maintenance loop starts it again after.
"""
import logging
import threading
import time

from utils.voice_client import RECV_AVAILABLE, voice_recv
from utils.voice_receive import (MONO_FRAME_WIDTH, SAMPLE_RATE, PacketDecoder,
                                 drain_socket, mono_segment, to_mono)

_log = logging.getLogger(__name__)

# How much history is kept. Memory is roughly 96KB/s per speaker who has spoken
# inside the window, so a minute is a few MB per active person.
WINDOW_SECONDS = 60
DEFAULT_CLIP_SECONDS = 30

buffers: dict[int, "ClipBuffer"] = {}
_disabled: set[int] = set()


class ClipBuffer:
    """Keeps the last `window_seconds` of each speaker on a shared timeline."""

    def __init__(self, voice_client, window_seconds: int = WINDOW_SECONDS):
        self.voice_client = voice_client
        self.window_seconds = window_seconds
        self.window_bytes = int(window_seconds * SAMPLE_RATE) * MONO_FRAME_WIDTH
        self.decoder = PacketDecoder(voice_client)

        # Byte 0 of every track is this instant; trimming moves it forward.
        self.origin = time.perf_counter()
        self.tracks: dict[int, bytearray] = {}
        self.names: dict[int, str] = {}
        self.last_write: dict[int, float] = {}
        self._lock = threading.Lock()

    def write(self, user, data) -> None:
        """Sink callback, on the reader thread. Never raise: that kills it."""
        try:
            self._write(user, data)
        except Exception:
            _log.exception("Dropped a packet from the clip buffer")

    def _write(self, user, data) -> None:
        if user is None:
            return
        pcm = to_mono(data.pcm or self.decoder.to_pcm(user.id, data) or b'')
        if not pcm:
            return

        now = time.perf_counter()
        name = getattr(user, 'display_name', str(user.id))
        with self._lock:
            elapsed = int((now - self.origin) * SAMPLE_RATE) * MONO_FRAME_WIDTH
            span = max((len(track) for track in self.tracks.values()), default=0)

            if elapsed - span > self.window_bytes:
                # Nobody has spoken for longer than the window, so everything
                # buffered has already expired. Restart the timeline here rather
                # than materialising the whole idle gap as zeros — an hour of
                # quiet would be ~345MB of padding before trimming ran.
                self.tracks.clear()
                self.names.clear()
                self.last_write.clear()
                self.origin = now
                elapsed = 0

            track = self.tracks.get(user.id)
            if track is None:
                track = self.tracks[user.id] = bytearray()
                self.names[user.id] = name

            # Positioned by wall clock so speakers stay aligned with each other,
            # but never rewound — jitter must not overwrite the previous packet.
            offset = max(len(track), elapsed)
            if offset > len(track):
                track += b'\x00' * (offset - len(track))
            track += pcm

            self.last_write[user.id] = now
            self._trim(now)

    def _trim(self, now: float) -> None:
        """Drop everything older than the window. Caller holds the lock."""
        span = max((len(track) for track in self.tracks.values()), default=0)
        excess = span - self.window_bytes
        if excess > 0:
            # Every track shares an origin, so they all lose the same prefix.
            for track in self.tracks.values():
                del track[:excess]
            self.origin += excess / MONO_FRAME_WIDTH / SAMPLE_RATE

        # A speaker silent for the whole window is pure padding: forget them.
        cutoff = now - self.window_seconds
        for user_id, when in list(self.last_write.items()):
            if when < cutoff:
                self.tracks.pop(user_id, None)
                self.names.pop(user_id, None)
                self.last_write.pop(user_id, None)

    def snapshot(self, seconds: float) -> tuple[dict, float]:
        """The last `seconds` of audio per speaker, plus how much we actually got."""
        want = int(seconds * SAMPLE_RATE) * MONO_FRAME_WIDTH
        with self._lock:
            span = max((len(track) for track in self.tracks.values()), default=0)
            if not span:
                return {}, 0.0

            length = min(want, span)
            start = span - length
            pieces = {}
            for user_id, track in self.tracks.items():
                piece = bytes(track[start:start + length])
                if not piece or not any(piece):
                    continue  # this speaker was silent for the whole clip
                # tracks end at different points; pad so they line up
                pieces[self.names[user_id]] = piece.ljust(length, b'\x00')

        return ({name: mono_segment(data) for name, data in pieces.items()},
                length / MONO_FRAME_WIDTH / SAMPLE_RATE)


# --- per-guild control -------------------------------------------------------

def is_enabled(guild) -> bool:
    return guild.id not in _disabled


def set_enabled(guild, enabled: bool) -> None:
    if enabled:
        _disabled.discard(guild.id)
    else:
        _disabled.add(guild.id)
        stop(guild)


def get(guild) -> "ClipBuffer | None":
    return buffers.get(guild.id)


def start(voice_client) -> bool:
    """Begin buffering, unless it's off here or the receiver is already in use."""
    if not RECV_AVAILABLE or voice_client is None:
        return False
    guild = voice_client.guild
    if guild.id in _disabled or guild.id in buffers:
        return False
    if not isinstance(voice_client, voice_recv.VoiceRecvClient):
        return False
    if not voice_client.is_connected():
        # Still handshaking: there's no socket yet and listen() would refuse.
        # The maintenance loop tries again once the connection is up.
        return False
    if voice_client.is_listening():
        return False  # a recording or a call has it

    # Nothing has read the socket since we last listened; without this the
    # backlog arrives as a burst of stale audio stamped as "now".
    drain_socket(voice_client)

    buffer = ClipBuffer(voice_client)
    try:
        voice_client.listen(voice_recv.BasicSink(buffer.write, decode=False))
    except Exception:
        _log.exception("Couldn't start the clip buffer in %s", guild.id)
        return False

    buffers[guild.id] = buffer
    _log.debug("Clip buffer running in %s", guild.id)
    return True


def claim_receiver(guild) -> bool:
    """Free the guild's voice receiver for a recording or a call, and report
    whether it's actually available now.

    Releasing and checking are one operation on purpose. The buffer holds the
    receiver whenever the bot is idle in a channel, so a caller that checked
    `is_listening()` before releasing would reject every recording or call where
    the bot was already sitting in the channel.
    """
    stop(guild)
    voice_client = guild.voice_client
    return voice_client is not None and not voice_client.is_listening()


def stop(guild) -> bool:
    """Give the receiver back. Anything buffered is discarded."""
    buffer = buffers.pop(guild.id, None)
    if buffer is None:
        return False
    try:
        buffer.voice_client.stop_listening()
    except Exception:
        _log.debug("stop_listening failed in %s", guild.id, exc_info=True)
    return True
