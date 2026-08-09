import asyncio
from io import BytesIO
import logging
import threading
import time
from typing import Any, Callable, Optional
import math
import typing
import queue

import discord
from discord import VoiceClient
from discord.enums import SpeakingState
from discord.opus import Encoder as OpusEncoder, OPUS_SILENCE
from pydub import AudioSegment, effects
import pydub
import traceback

from utils.voice_client import VoiceClientCls

_log = logging.getLogger(__name__)


class AudioPlayer(threading.Thread):
    """
    This class is a thread that continuously mixes audio from multiple sources
    and plays it back in a Discord voice channel.

    It uses a producer-consumer model with a continuous stream buffer and
    direct source audio consumption to ensure perfectly seamless, click-free playback.
    """
    DELAY: float = OpusEncoder.FRAME_LENGTH / 1000.0
    SAMPLES_PER_FRAME: int = OpusEncoder.SAMPLES_PER_FRAME
    CHANNELS: int = 2
    SAMPLE_WIDTH: int = 2 # 16-bit audio
    SAMPLING_RATE: int = OpusEncoder.SAMPLING_RATE
    FRAME_WIDTH: int = CHANNELS * SAMPLE_WIDTH
    FRAME_MS: int = int(OpusEncoder.FRAME_LENGTH)
    BATCH_MS: int = 100

    # How many pre-mixed frames we keep ahead of the consumer. This is the
    # jitter buffer, but it is also how long a stop takes to become audible,
    # so it stays short: 20 frames = 400ms.
    QUEUE_FRAMES: int = 20

    # Most a live source will buffer before it starts dropping the oldest audio.
    # This is the ceiling on added delay for a call: 1 second.
    STREAM_MAX_BYTES: int = SAMPLING_RATE * CHANNELS * SAMPLE_WIDTH

    # Sources are normalized to this much headroom rather than to full scale.
    # Overlaying is the whole point of this player, and two sources normalized
    # to 0 dBFS sum well past full scale and clip hard in audioop.add.
    MIX_HEADROOM_DB: float = 6.0

    def __init__(
        self,
        client: VoiceClient,
        encoder: OpusEncoder,
        *,
        after: Optional[Callable[[Optional[Exception]], Any]] = None,
    ) -> None:
        super().__init__(daemon=True, name=f'audio-consumer:{id(self):#x}')
        self.client: VoiceClient = client
        self.after: Optional[Callable[[Optional[Exception]], Any]] = after

        # Core threading events and locks
        self._end: threading.Event = threading.Event()
        self._sources_exist = threading.Event()
        self._sources_exist.clear()
        self._lock: threading.RLock = threading.RLock()

        # Queue for final, perfectly-sized raw audio frames
        self.processed_queue = queue.Queue(maxsize=self.QUEUE_FRAMES)
        self._silence_cache: Optional[AudioSegment] = None

        self._current_error: Optional[Exception] = None
        
        if after is not None and not callable(after):
            raise TypeError('Expected a callable for the "after" parameter.')
        
        self.encoder = encoder

        # Audio data dictionaries
        self.userDict: dict[str, dict[str, Any]] = {}
        self.pausedUserDict: dict[str, dict[str, Any]] = {}
        
        # Real-time effects parameters
        self.pitch: float = 1.0
        self.volume: int = 100

        # The producer thread that prepares audio frames
        self.producer_thread = threading.Thread(target=self._producer_loop, daemon=True, name=f'audio-producer:{id(self):#x}')

    def start(self):
        """Starts both the producer and consumer threads."""
        self.producer_thread.start()
        super().start()

    def _producer_loop(self) -> None:
        """
        The "producer" part of the pattern.
        Its job is to maintain a continuous stream of processed audio
        and slice 20ms frames from it into the processed_queue.
        """
        continuous_stream_buffer = AudioSegment.empty()

        while not self._end.is_set():
            self._sources_exist.wait()

            if self._end.is_set():
                break

            # 1. Refill the continuous stream buffer if it's running low.
            if len(continuous_stream_buffer) < self.BATCH_MS:
                batch_to_add = self._generate_processed_batch(duration_ms=self.BATCH_MS)
                if batch_to_add:
                    continuous_stream_buffer += batch_to_add
                else:
                    # Every source is spent. Flush the partial frame that's left
                    # over rather than dropping it, then idle until woken.
                    continuous_stream_buffer = self._drain_buffer(
                        continuous_stream_buffer, flush=True)
                    with self._lock:
                        idle = not self.userDict
                        if idle:
                            self._sources_exist.clear()
                    if not idle:
                        # Sources are loaded but produced nothing, so mixing is
                        # failing. Back off instead of retrying flat out.
                        time.sleep(self.DELAY)
                    continue

            # 2. Slice 20ms frames from the continuous stream into the queue.
            continuous_stream_buffer = self._drain_buffer(continuous_stream_buffer)

    def _drain_buffer(self, buffer: AudioSegment, flush: bool = False) -> AudioSegment:
        """Slice whole frames out of the buffer into the queue, and return what
        didn't fit in a frame. Returns early if we're shutting down."""
        while len(buffer) >= self.FRAME_MS:
            frame, rest = buffer[:self.FRAME_MS], buffer[self.FRAME_MS:]
            if not self.output_processed_frame(frame):
                return buffer  # shutting down; don't lose the audio
            buffer = rest

        if flush and len(buffer) > 0:
            self.output_processed_frame(buffer)
            return AudioSegment.empty()
        return buffer

    def output_processed_frame(self, frame: AudioSegment) -> bool:
        """Sanitize a frame to exactly one Opus frame and hand it to the
        consumer, blocking while the queue is full.

        Blocking is what paces the producer against the 20ms consumer. Polling
        `processed_queue.full()` in a loop instead spins a core at 100%, which
        is what this used to do."""
        expected_bytes = self.SAMPLES_PER_FRAME * self.FRAME_WIDTH
        frame_data = frame.raw_data
        current_bytes = len(frame_data)

        if current_bytes < expected_bytes:
            frame_data += b'\x00' * (expected_bytes - current_bytes)
        elif current_bytes > expected_bytes:
            frame_data = frame_data[:expected_bytes]

        while not self._end.is_set():
            try:
                self.processed_queue.put(frame_data, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def _silence(self, frames: int) -> AudioSegment:
        """A stereo silence bed to mix onto, cached between batches.

        AudioSegment.silent() is mono, and a mono buffer reaching the encoder
        gets read as interleaved stereo — half speed and wrong pitch.
        """
        cached = self._silence_cache
        if cached is None or len(cached.raw_data) != frames * self.FRAME_WIDTH:
            cached = AudioSegment(
                b'\x00' * (frames * self.FRAME_WIDTH),
                metadata={'channels': self.CHANNELS,
                          'sample_width': self.SAMPLE_WIDTH,
                          'frame_rate': self.SAMPLING_RATE,
                          'frame_width': self.FRAME_WIDTH},
            )
            self._silence_cache = cached
        return cached

    def _read_frames(self, data: dict, frames: int) -> Optional[AudioSegment]:
        """Take the next `frames` frames from a source and advance its cursor.

        Sources are read through an integer cursor rather than being re-sliced.
        `segment = segment[read:]` copies the entire remainder of the track on
        every batch — for a 5 minute song that's ~57MB memcpy'd 10x a second.
        """
        if data.get('live'):
            return self._read_live(data, frames)

        segment: AudioSegment = data['segment']
        start = data['pos']
        end = min(start + frames, data['frames'])
        if end <= start:
            return None

        width = segment.frame_width
        chunk = segment._spawn(segment.raw_data[start * width:end * width])
        data['pos'] = end
        return chunk

    def _read_live(self, data: dict, frames: int) -> Optional[AudioSegment]:
        """Take whatever a live source has buffered, up to `frames`.

        Unlike a finite source this one is never 'done' — running dry just means
        the far end isn't talking right now, so it yields nothing and stays put.
        """
        buffer: bytearray = data['buffer']
        if not buffer:
            return None

        wanted = frames * self.FRAME_WIDTH
        chunk = bytes(buffer[:wanted])
        del buffer[:len(chunk)]
        return AudioSegment(chunk, metadata={'channels': self.CHANNELS,
                                             'sample_width': self.SAMPLE_WIDTH,
                                             'frame_rate': self.SAMPLING_RATE,
                                             'frame_width': self.FRAME_WIDTH})

    # --- live sources ----------------------------------------------------

    def open_stream(self, user: str) -> None:
        """Register a source that audio can be fed to over time.

        Finite sources are dropped the moment the cursor reaches the end. A
        live one stays registered through the gaps, which is what a call needs.
        """
        with self._lock:
            existing = self.userDict.get(user)
            if existing is not None and existing.get('live'):
                return
            self.pausedUserDict.pop(user, None)
            self.userDict[user] = {'live': True, 'buffer': bytearray()}

    def feed_stream(self, user: str, pcm: bytes) -> bool:
        """Append PCM (48kHz stereo 16-bit) to a live source.

        False means there's no such live source — the caller may need to
        re-open it, e.g. if this player replaced one that had it registered.
        """
        with self._lock:
            source = self.userDict.get(user)
            if source is None or not source.get('live'):
                return False

            buffer: bytearray = source['buffer']
            buffer += pcm
            # If the far end runs even slightly fast, an unbounded buffer turns
            # into unbounded delay. Drop the oldest audio instead of drifting.
            overflow = len(buffer) - self.STREAM_MAX_BYTES
            if overflow > 0:
                del buffer[:overflow]
                source['dropped'] = source.get('dropped', 0) + overflow

            self._sources_exist.set()
            return True

    def close_stream(self, user: str) -> None:
        with self._lock:
            source = self.userDict.get(user)
            if source is not None and source.get('live'):
                del self.userDict[user]

    def _generate_processed_batch(self, duration_ms: int) -> Optional[AudioSegment]:
        """
        Generates a batch of mixed and processed audio by directly consuming the source
        AudioSegments, which is the most robust way to prevent skipping.
        """
        with self._lock:
            if not self.userDict:
                return None

            # Snapshot the live knobs so the batch is internally consistent even
            # if /pitch or /volume lands halfway through mixing it.
            pitch = self.pitch
            volume = self.volume

            # Determine how much source audio to read, based on pitch
            try:
                new_sample_rate = self.SAMPLING_RATE * (2.0 ** (pitch - 1.0))
                speed_multiplier = new_sample_rate / self.SAMPLING_RATE
                # NaN/inf don't raise here, they just poison the frame count
                if not math.isfinite(speed_multiplier) or speed_multiplier <= 0:
                    raise ValueError("non-finite speed multiplier")
            except (ValueError, ZeroDivisionError, OverflowError) as exc:
                _log.warning(f"Invalid pitch value {pitch} ({exc}), falling back to 1.0x")
                pitch, new_sample_rate, speed_multiplier = 1.0, float(self.SAMPLING_RATE), 1.0

            # Work in whole frames: repeated millisecond slicing rounds each
            # boundary independently and can drift off a sample.
            frames_to_read = max(1, int(round(
                self.SAMPLING_RATE * duration_ms * speed_multiplier / 1000)))

            # 1. Mix a batch from all sources.
            chunks = []
            for user, data in list(self.userDict.items()):
                chunk = self._read_frames(data, frames_to_read)
                if chunk is not None:
                    chunks.append(chunk)
                # live sources stay registered through silence; finite ones go
                # as soon as they're spent
                if not data.get('live') and data['pos'] >= data['frames']:
                    del self.userDict[user]

        if not chunks:
            return None

        if len(chunks) == 1:
            # The common case is a single source (just music, or just a clip).
            # Overlaying it onto a silence bed would be a pointless full copy.
            mixed_batch = chunks[0]
        else:
            # Sources can be different lengths near the end of a track, so mix
            # onto a full-length bed — overlay() truncates to the base.
            mixed_batch = self._silence(frames_to_read)
            for chunk in chunks:
                mixed_batch = mixed_batch.overlay(chunk)

        # 2. Apply effects to the entire batch at once.
        try:
            processed_batch = mixed_batch
            if pitch != 1.0:
                pitched_sound = processed_batch._spawn(processed_batch.raw_data, overrides={'frame_rate': math.floor(new_sample_rate)})
                processed_batch = pitched_sound.set_frame_rate(self.SAMPLING_RATE)
            if volume != 100:
                if volume > 0:
                    gain = 20 * math.log10(volume / 100.0)
                    processed_batch = processed_batch.apply_gain(gain)
                else:
                    processed_batch = self._silence(
                        len(processed_batch.raw_data) // self.FRAME_WIDTH)

            return processed_batch
        except Exception as e:
            _log.error(f"Error processing audio batch: {e}")
            return None

    def _do_run(self) -> None:
        """ The "consumer" part of the pattern. Now extremely simple. """
        client = self.client
        play_audio = client.send_audio_packet
        self._speak(SpeakingState.voice)

        startTimer = time.perf_counter()
        loops = 0
        was_idle = False # <--- 1. Add this flag

        while not self._end.is_set():
            try:
                frame_data = self.processed_queue.get(timeout=0.1)
                
                if not client.is_connected():
                    _log.warning('Voice client disconnected, consumer is pausing.')
                    client.wait_until_connected()
                    _log.info('Reconnected successfully, consumer is resuming.')
                    startTimer = time.perf_counter()
                    loops = 0

                # ---> 2. Reset the timer if we just came back from being idle <---
                if was_idle:
                    startTimer = time.perf_counter()
                    loops = 0
                    was_idle = False
                    self._speak(SpeakingState.voice)

                loops += 1
                opusData = self.encoder.encode(frame_data, self.SAMPLES_PER_FRAME)
                play_audio(opusData, encode=False)

                next_tick = startTimer + (self.DELAY * loops)
                delay = max(0, next_tick - time.perf_counter())
                time.sleep(delay)

            except queue.Empty:
                # ---> 3. Set the flag when the queue runs dry <---
                if not was_idle:
                    # Five frames tells Discord's jitter buffer the stream
                    # stopped; streaming silence forever instead just keeps the
                    # speaking indicator lit and wastes packets.
                    self.send_silence(5)
                    self._speak(SpeakingState.none)
                    was_idle = True
                continue
            except Exception as e:
                _log.error(f"Error in consumer loop: {e}")
                # don't spin the thread if this keeps failing
                time.sleep(self.DELAY)

    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:
            _log.exception("An unhandled exception occurred in the audio player thread.")
            self._current_error = exc
        finally:
            self._speak(SpeakingState.none)
            _log.info("Audio player consumer thread has finished.")

    def _discard_prepared_audio(self) -> None:
        """Throw away pre-mixed frames so a stop is heard now rather than after
        the queue drains.

        Only safe when nothing is left playing: queued frames are a mix of every
        source, so dropping them to stop one would cut off the others too.
        """
        while True:
            try:
                self.processed_queue.get_nowait()
            except queue.Empty:
                return

    def stop(self):
        """Stops the player and clears all queues."""
        self._end.set()
        self._sources_exist.set()
        with self._lock:
            self.userDict.clear()
            self.pausedUserDict.clear()
        self._discard_prepared_audio()
        _log.info("AudioPlayer stop called.")

    def stop_user(self, user: str):
        with self._lock:
            self.userDict.pop(user, None)
            self.pausedUserDict.pop(user, None)
            others_playing = bool(self.userDict)
        if not others_playing:
            self._discard_prepared_audio()

    def pause_user(self, user: str):
        # Deliberately keeps the prepared frames: their audio is already past
        # the source cursor, so discarding them would lose it on resume. The
        # cost is a short tail after the click, not a gap.
        with self._lock:
            if user in self.userDict:
                self.pausedUserDict[user] = self.userDict.pop(user)
    
    def resume_user(self, user: str):
        with self._lock:
            if user in self.pausedUserDict:
                self.userDict[user] = self.pausedUserDict.pop(user)
                self._sources_exist.set()

    def _speak(self, speaking: SpeakingState) -> None:
        try:
            if self.client.client.loop.is_running():
                asyncio.run_coroutine_threadsafe(self.client.ws.speak(speaking), self.client.client.loop)
        except Exception:
            pass

    def send_silence(self, count: int = 1) -> None:
        try:
            for _ in range(count):
                self.client.send_audio_packet(OPUS_SILENCE, encode=False)
        except Exception:
            pass
    
    def is_playing(self, user: str) -> bool:
        with self._lock:
            return user in self.userDict or user in self.pausedUserDict

    def is_paused(self, user: str) -> bool:
        with self._lock:
            return user in self.pausedUserDict

    def remaining_ms(self, user: str) -> Optional[int]:
        """Milliseconds of audio left for a source, or None if it isn't loaded.

        Sources are read through a cursor, so how much is left of one is also
        how far into it we are.
        """
        with self._lock:
            data = self.userDict.get(user) or self.pausedUserDict.get(user)
            if data is None:
                return None
            if data.get('live'):
                frames_left = len(data['buffer']) // self.FRAME_WIDTH
            else:
                frames_left = max(0, data['frames'] - data['pos'])
            return int(frames_left * 1000 / self.SAMPLING_RATE)

    def change_pitch(self, newPitch: float):
        if not (0.25 <= newPitch <= 4.0):
            return
        if newPitch == self.pitch:
            return
        
        with self._lock:
            _log.info(f"Changing pitch to {newPitch}x")
            self.pitch = newPitch
    
    def add_to_source_queue(self, newSound: AudioSegment, user: str):
        """Hand a source to the mixer, replacing whatever that identifier was
        already playing.

        Conversion and normalization walk the whole segment, so call this off
        the event loop for anything longer than a soundboard clip.
        """
        newSound = (newSound.set_frame_rate(self.SAMPLING_RATE)
                            .set_channels(self.CHANNELS)
                            .set_sample_width(self.SAMPLE_WIDTH))
        newSound = effects.normalize(newSound, headroom=self.MIX_HEADROOM_DB)

        _log.debug(f"Adding new audio source for user {user} with length {len(newSound)}ms.")

        source = {
            'segment': newSound,
            'pos': 0,
            'frames': len(newSound.raw_data) // newSound.frame_width,
        }

        with self._lock:
            self.pausedUserDict.pop(user, None)
            self.userDict[user] = source
            self._sources_exist.set()
    
    def set_volume(self, volume: int):
        self.volume = max(0, min(200, volume))

# --- Global Bot State Management ---

audioClients: typing.Dict[discord.Guild, AudioPlayer] = {}
audioVolume: typing.Dict[discord.Guild, int] = {}

# Percent, converted to gain as 20*log10(v/100), so 100 is unity.
DEFAULT_VOLUME = 100


def make_encoder() -> OpusEncoder:
    """A fresh encoder per player — never share one.

    An Opus encoder carries mutable per-stream state and is not thread-safe.
    Each AudioPlayer encodes on its own consumer thread, so a shared encoder is
    fine right up until the bot is in two guilds at once (a call, or music in
    two servers). Then the state corrupts and libopus aborts the *process*:
    "silk/resampler.c: assertion failed: inLen >= S->Fs_in_kHz".
    """
    return discord.opus.Encoder(
        application='audio',
        bitrate=128,
        fec=True,
        expected_packet_loss=0.15,
        bandwidth='full',
        signal_type='auto',
    )

async def init_voice_client(inter: discord.Interaction) -> bool:
    guild = inter.guild
    if not guild:
        return False
        
    if not isinstance(inter.user, discord.Member) or not inter.user.voice or not inter.user.voice.channel:
        message = "You need to be in a voice channel to use this command."
        # callers often defer first, in which case responding again would raise
        if inter.response.is_done():
            await inter.followup.send(message, ephemeral=True)
        else:
            await inter.response.send_message(message, ephemeral=True)
        return False

    return await connect_to(inter.user.voice.channel)


async def connect_to(channel) -> bool:
    """Join (or move to) `channel` and make sure a live AudioPlayer is attached.

    Split out of init_voice_client so callers that aren't acting on behalf of
    the interaction's author — a call being accepted on the far side, say — can
    connect somewhere the invoking user isn't.
    """
    guild = channel.guild

    # Connect or move to the correct channel. A guild gets one voice client, so
    # it's always the recording-capable subclass — see utils/voice_client.py.
    if guild.voice_client is None:
        await channel.connect(cls=VoiceClientCls)
    elif guild.voice_client.channel != channel:
        await guild.voice_client.move_to(channel)

    if guild not in audioVolume:
        audioVolume[guild] = DEFAULT_VOLUME

    # Grab the current, active VoiceClient object
    vc = typing.cast(VoiceClient, guild.voice_client)

    # Check if we need to replace the player
    player_exists = guild in audioClients
    
    # We replace the player if it's dead, OR if it's attached to an old/kicked connection
    if (not player_exists or 
        not audioClients[guild].is_alive() or 
        not audioClients[guild].producer_thread.is_alive() or 
        audioClients[guild].client != vc): # <--- THIS IS THE FIX
        
        if player_exists:
            audioClients[guild].stop() # Tell the zombie thread to shut down

        _log.info(f"Initializing new AudioPlayer for guild {guild.id}")
        audioClients[guild] = AudioPlayer(vc, make_encoder())
        audioClients[guild].start()
        audioClients[guild].set_volume(audioVolume[guild])

    return True

async def play(inter: discord.Interaction, sound: AudioSegment, identifier: str):
    guild = inter.guild
    if guild and await init_voice_client(inter):
        # normalizing a full song is a multi-hundred-ms walk over tens of MB;
        # doing it inline stalls every other command on the bot
        await asyncio.to_thread(audioClients[guild].add_to_source_queue, sound, identifier)

async def disconnect_voice(guild: discord.Guild) -> bool:
    """Stop the player and leave the voice channel. False if we weren't in one.

    The player goes first so its consumer thread stops writing to a client
    that's about to close.
    """
    player = audioClients.pop(guild, None)
    if player is not None:
        player.stop()
    audioVolume.pop(guild, None)

    vc = guild.voice_client
    if vc is None:
        return False
    try:
        vc.stop_listening()
    except Exception:
        pass  # only exists on the recv client, and only while listening
    await vc.disconnect(force=True)
    return True

def open_stream(guild: discord.Guild, identifier: str) -> bool:
    """Start a live source in `guild`'s mixer. Guild-based rather than
    interaction-based: a bridge feeds a guild the invoking user isn't in."""
    player = audioClients.get(guild)
    if player is None:
        return False
    player.open_stream(identifier)
    return True

def feed_stream(guild: discord.Guild, identifier: str, pcm: bytes) -> bool:
    player = audioClients.get(guild)
    return player.feed_stream(identifier, pcm) if player is not None else False

def close_stream(guild: discord.Guild, identifier: str) -> None:
    player = audioClients.get(guild)
    if player is not None:
        player.close_stream(identifier)

def set_volume(inter: discord.Interaction, volume: int):
    guild = inter.guild
    if guild:
        audioVolume[guild] = volume
        if guild in audioClients and audioClients[guild] is not None:
            audioClients[guild].set_volume(volume)

def is_playing(inter: discord.Interaction, identifier: str) -> bool:
    guild = inter.guild
    if guild in audioClients and audioClients[guild] is not None:
        return audioClients[guild].is_playing(identifier)
    return False

def is_paused(inter: discord.Interaction, identifier: str) -> bool:
    guild = inter.guild
    if guild in audioClients and audioClients[guild] is not None:
        return audioClients[guild].is_paused(identifier)
    return False

def remaining_ms(inter: discord.Interaction, identifier: str) -> Optional[int]:
    guild = inter.guild
    if guild in audioClients and audioClients[guild] is not None:
        return audioClients[guild].remaining_ms(identifier)
    return None

def stop_all(inter: discord.Interaction):
    guild = inter.guild
    if guild in audioClients and audioClients[guild] is not None:
        audioClients[guild].stop()
        audioClients.pop(guild, None)
        audioVolume.pop(guild, None)

def stop_user(inter: discord.Interaction, user: str):
    guild = inter.guild
    if guild in audioClients and audioClients[guild] is not None:
        audioClients[guild].stop_user(user)

def pause_user(inter: discord.Interaction, user: str):
    guild = inter.guild
    if guild in audioClients and audioClients[guild] is not None:
        audioClients[guild].pause_user(user)

def resume_user(inter: discord.Interaction, user: str):
    guild = inter.guild
    if guild in audioClients and audioClients[guild] is not None:
        audioClients[guild].resume_user(user)

async def change_pitch(inter: discord.Interaction, pitch: float):
    guild = inter.guild
    if guild and await init_voice_client(inter):
        audioClients[guild].change_pitch(pitch)
