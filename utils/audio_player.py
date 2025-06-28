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

_log = logging.getLogger(__name__)


class AudioPlayer(threading.Thread):
    """
    This class is a thread that continuously mixes audio from multiple sources
    and plays it back in a Discord voice channel.

    It uses a producer-consumer model with sample-accurate dynamic slicing to ensure
    click-free, pitch-shifted audio playback.
    """
    DELAY: float = OpusEncoder.FRAME_LENGTH / 1000.0
    SAMPLES_PER_FRAME: int = OpusEncoder.SAMPLES_PER_FRAME
    CHANNELS: int = 2
    SAMPLE_WIDTH: int = 2 # 16-bit audio
    SAMPLING_RATE: int = OpusEncoder.SAMPLING_RATE

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
        self._items_in_queue = threading.Event() # Signals the producer to start working
        self._items_in_queue.clear()
        self._lock: threading.RLock = threading.RLock()

        # Producer-Consumer queue. Holds perfectly-sized, ready-to-encode 20ms audio frames.
        self.processed_queue = queue.Queue(maxsize=50) 

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

    def _is_queue_empty(self) -> bool:
        """Helper to check if the userDict is empty under lock."""
        with self._lock:
            return not self.userDict

    def _generate_frame(self) -> Optional[bytes]:
        """
        The core of the producer. Generates a single, perfectly-sized 20ms frame.
        This function is now based on sample counts, not milliseconds, to avoid float rounding errors.
        """
        try:
            # 1. Calculate how many source samples are needed to produce one output frame (960 samples).
            speed_multiplier = 2.0 ** (self.pitch - 1.0)
            source_samples_to_process = int(round(self.SAMPLES_PER_FRAME * speed_multiplier))
        except (ValueError, ZeroDivisionError):
            source_samples_to_process = self.SAMPLES_PER_FRAME
        
        if source_samples_to_process <= 0:
            return None

        # 2. Mix a chunk of `source_samples_to_process` from all playing tracks.
        mixed_chunk: Optional[AudioSegment] = None
        with self._lock:
            if self.userDict:
                # We need to convert our sample-based progress into milliseconds for pydub's slicing.
                source_duration_ms = source_samples_to_process * 1000.0 / self.SAMPLING_RATE
                mixed_chunk = AudioSegment.silent(duration=source_duration_ms, frame_rate=self.SAMPLING_RATE)
                
                users_to_remove = set()
                for user, data in self.userDict.items():
                    segment: AudioSegment = data['segment']
                    progress_samples: int = data['progress_samples']
                    total_samples = len(segment.get_array_of_samples()) // self.CHANNELS

                    if progress_samples < total_samples:
                        # Convert sample indices to millisecond indices for pydub slicing
                        start_ms = progress_samples * 1000.0 / self.SAMPLING_RATE
                        end_ms = (progress_samples + source_samples_to_process) * 1000.0 / self.SAMPLING_RATE
                        
                        current_chunk = segment[start_ms:end_ms]
                        
                        mixed_chunk = mixed_chunk.overlay(current_chunk)
                        data['progress_samples'] += source_samples_to_process
                    else:
                        users_to_remove.add(user)
                
                for user in users_to_remove:
                    del self.userDict[user]
        
        if mixed_chunk is None or len(mixed_chunk) == 0:
            return None

        # 3. Apply effects to the mixed chunk.
        try:
            # Apply pitch shifting by changing the frame rate.
            if self.pitch != 1.0:
                new_sample_rate = int(mixed_chunk.frame_rate * speed_multiplier)
                pitched_sound = mixed_chunk._spawn(mixed_chunk.raw_data, overrides={'frame_rate': new_sample_rate})
                processed_frame = pitched_sound.set_frame_rate(self.SAMPLING_RATE)
            else:
                processed_frame = mixed_chunk

            # Apply volume
            if self.volume != 100:
                if self.volume > 0:
                    gain = 20 * math.log10(self.volume / 100.0)
                    processed_frame = processed_frame.apply_gain(gain)
                else:
                    processed_frame = AudioSegment.silent(duration=20)
            
            # ** FINAL CLICKING FIX: Micro-crossfade **
            # Apply a tiny 1ms crossfade to the start and end of the frame.
            # This smooths the transition between frames, eliminating clicks caused by waveform discontinuities.
            if self.pitch != 1.0:
                if len(processed_frame) > 2:  # Ensure there's enough data to crossfade
                    processed_frame = processed_frame.fade_in(1).fade_out(1)

        except Exception as e:
            _log.error(f"Error during audio processing: {e}")
            return None
        
        # 4. Sanitize the final frame to ensure it's exactly the right size.
        expected_bytes = self.SAMPLES_PER_FRAME * self.CHANNELS * self.SAMPLE_WIDTH
        frame_data = processed_frame.raw_data
        current_bytes = len(frame_data)
        
        if current_bytes < expected_bytes:
            frame_data += b'\x00' * (expected_bytes - current_bytes)
        elif current_bytes > expected_bytes:
            frame_data = frame_data[:expected_bytes]
            
        return frame_data

    def _producer_loop(self) -> None:
        """
        The "producer" part of the pattern.
        This loop continuously generates frames and puts them in the queue.
        """
        while not self._end.is_set():
            self._items_in_queue.wait()

            while not self._is_queue_empty():
                if self._end.is_set():
                    break
                
                if self.processed_queue.full():
                    time.sleep(self.DELAY)
                    continue

                frame_data = self._generate_frame()
                
                if frame_data:
                    try:
                        self.processed_queue.put(frame_data, block=False)
                    except queue.Full:
                        pass
                else:
                    # No more frames to generate from the current sources
                    break
            
            with self._lock:
                if not self.userDict:
                    self._items_in_queue.clear()

    def _clear_processed_queue(self):
        """Safely empties the processed queue."""
        while not self.processed_queue.empty():
            try:
                self.processed_queue.get_nowait()
            except queue.Empty:
                break

    def _do_run(self) -> None:
        """
        The "consumer" part of the pattern.
        This loop sends audio from the processed_queue to Discord at a precise interval.
        """
        client = self.client
        play_audio = client.send_audio_packet
        self._speak(SpeakingState.voice)

        startTimer = time.perf_counter()
        loops = 0
        
        while not self._end.is_set():
            try:
                frame_data = self.processed_queue.get(timeout=0.1)
                
                if not client.is_connected():
                    _log.warning('Voice client disconnected, consumer is pausing.')
                    client.wait_until_connected()
                    _log.info('Reconnected successfully, consumer is resuming.')
                    startTimer = time.perf_counter()
                    loops = 0
                
                loops += 1
                opusData = self.encoder.encode(frame_data, self.SAMPLES_PER_FRAME)
                play_audio(opusData, encode=False)
                
                next_tick = startTimer + (self.DELAY * loops)
                delay = max(0, next_tick - time.perf_counter())
                time.sleep(delay)

            except queue.Empty:
                self.send_silence(1)
                continue
            except Exception as e:
                _log.error(f"Error in consumer loop: {e}")


    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:
            _log.exception("An unhandled exception occurred in the audio player thread.")
            self._current_error = exc
        finally:
            self._speak(SpeakingState.none)
            _log.info("Audio player consumer thread has finished.")

    def stop(self):
        """Stops the player and clears all queues."""
        self._end.set()
        self._items_in_queue.set()
        with self._lock:
            self.userDict.clear()
            self.pausedUserDict.clear()
            self._clear_processed_queue()
        _log.info("AudioPlayer stop called.")
    
    def stop_user(self, user: str):
        with self._lock:
            self.userDict.pop(user, None)
            self.pausedUserDict.pop(user, None)
            self._clear_processed_queue()
            
    def pause_user(self, user: str):
        with self._lock:
            if user in self.userDict:
                self.pausedUserDict[user] = self.userDict.pop(user)
                self._clear_processed_queue()
    
    def resume_user(self, user: str):
        with self._lock:
            if user in self.pausedUserDict:
                self.userDict[user] = self.pausedUserDict.pop(user)
                self._items_in_queue.set()

    def _speak(self, speaking: SpeakingState) -> None:
        try:
            if self.client.client.loop.is_running():
                asyncio.run_coroutine_threadsafe(self.client.ws.speak(speaking), self.client.client.loop)
        except Exception:
            _log.exception("Speaking call in player failed")

    def send_silence(self, count: int = 1) -> None:
        """Sends a few frames of silence to clear buffers."""
        try:
            for _ in range(count):
                self.client.send_audio_packet(OPUS_SILENCE, encode=False)
        except Exception:
            pass
    
    def is_playing(self, user: str) -> bool:
        with self._lock:
            return user in self.userDict or user in self.pausedUserDict
    
    def change_pitch(self, newPitch: float):
        if not (0.25 <= newPitch <= 4.0):
            _log.warning(f"Pitch value {newPitch} is outside the recommended range (0.25-4.0).")
            return
        if newPitch == self.pitch:
            return
        
        with self._lock:
            _log.info(f"Changing pitch to {newPitch}x")
            self.pitch = newPitch
    
    def add_to_source_queue(self, newSound: AudioSegment, user: str):
        with self._lock:
            if user in self.pausedUserDict:
                self.pausedUserDict.pop(user)

        if newSound.frame_rate != 48000 or newSound.channels != 2 or newSound.sample_width != 2:
            newSound = newSound.set_frame_rate(48000).set_channels(2).set_sample_width(2)
        
        newSound = effects.normalize(newSound)

        _log.debug(f"Adding new audio source for user {user} with length {len(newSound)}ms.")
        
        with self._lock:
            self.userDict[user] = {'segment': newSound, 'progress_samples': 0}
            
            # ** SEAMLESS OVERLAY LOGIC **
            # By NOT clearing the buffer, we ensure there are no gaps.
            # The producer will naturally start mixing in the new sound
            # as it generates frames, resulting in a slight (but seamless) delay
            # before the new overlay is heard.
            
            # Wake up the main producer loop to start processing.
            self._items_in_queue.set()
    
    def set_volume(self, volume: int):
        self.volume = max(0, min(200, volume))
        _log.info(f"Master volume set to {self.volume}%")

# --- Global Bot State Management ---

audioClients: typing.Dict[discord.Guild, AudioPlayer] = {}
audioVolume: typing.Dict[discord.Guild, int] = {}
encoder = discord.opus.Encoder(
    application='audio',
    bitrate=128,
    fec=True,
    expected_packet_loss=0.15,
    bandwidth='full',
    signal_type='auto',
)

async def init_voice_client(inter: discord.Interaction) -> bool:
    """Ensures the bot is connected to the user's voice channel and the player is running."""
    guild = inter.guild
    if not guild: return False
    if not isinstance(inter.user, discord.Member) or not inter.user.voice or not inter.user.voice.channel:
        await inter.response.send_message("You need to be in a voice channel to use this command.", ephemeral=True)
        return False

    user_channel = inter.user.voice.channel
    
    if guild.voice_client is None:
        await user_channel.connect()
    elif guild.voice_client.channel != user_channel:
        await guild.voice_client.move_to(user_channel)

    if guild not in audioVolume:
        audioVolume[guild] = 100

    if (guild not in audioClients or
            not audioClients[guild].is_alive() or
            not audioClients[guild].producer_thread.is_alive()):
        
        if guild in audioClients and (audioClients[guild].is_alive() or audioClients[guild].producer_thread.is_alive()):
            audioClients[guild].stop()
        
        _log.info(f"Initializing new AudioPlayer for guild {guild.id}")
        vc = typing.cast(VoiceClient, guild.voice_client)
        audioClients[guild] = AudioPlayer(vc, encoder)
        audioClients[guild].start()
        audioClients[guild].set_volume(audioVolume[guild])
        
    return True

async def play(inter: discord.Interaction, sound: AudioSegment, identifier: str):
    guild = inter.guild
    if guild and await init_voice_client(inter):
        audioClients[guild].add_to_source_queue(sound, identifier)

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
