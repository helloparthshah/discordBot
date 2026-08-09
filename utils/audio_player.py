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

    It uses a producer-consumer model with a continuous stream buffer and
    direct source audio consumption to ensure perfectly seamless, click-free playback.
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
        self._sources_exist = threading.Event()
        self._sources_exist.clear()
        self._lock: threading.RLock = threading.RLock()

        # Queue for final, perfectly-sized raw audio frames
        self.processed_queue = queue.Queue(maxsize=100) 

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
            if len(continuous_stream_buffer) < 100: # Maintain a buffer of at least 20ms
                batch_to_add = self._generate_processed_batch(duration_ms=100)
                if batch_to_add:
                    continuous_stream_buffer += batch_to_add
                elif len(continuous_stream_buffer) == 0:
                    # No more audio to process, wait for new sources
                    with self._lock:
                        if not self.userDict:
                           self._sources_exist.clear()
                    continue
                else:
                    # output the remaining buffer if it's not empty
                    if len(continuous_stream_buffer) > 0:
                        try:
                            self.output_processed_frame(continuous_stream_buffer)
                            continuous_stream_buffer = AudioSegment.empty()
                        except queue.Full:
                            print("Processed queue is full, skipping frame.")
                            pass
                        

            # 2. Slice 20ms frames from the continuous stream into the queue.
            while len(continuous_stream_buffer) >= 20:
                if self.processed_queue.full():
                    continue

                frame = continuous_stream_buffer[:20]
                continuous_stream_buffer = continuous_stream_buffer[20:]
                try:
                    self.output_processed_frame(frame)
                except queue.Full:
                    print("Processed queue is full, skipping frame.")
                    continuous_stream_buffer = frame + continuous_stream_buffer
                    pass
                
                # Sanitize the final frame to ensure it's exactly the right size.
    def output_processed_frame(self, frame: AudioSegment) -> None:
        expected_bytes = self.SAMPLES_PER_FRAME * self.CHANNELS * self.SAMPLE_WIDTH
        frame_data = frame.raw_data
        current_bytes = len(frame_data)
        
        if current_bytes < expected_bytes:
            frame_data += b'\x00' * (expected_bytes - current_bytes)
        elif current_bytes > expected_bytes:
            frame_data = frame_data[:expected_bytes]

        self.processed_queue.put(frame_data, block=False)
    
    def _generate_processed_batch(self, duration_ms: int) -> Optional[AudioSegment]:
        """
        Generates a batch of mixed and processed audio by directly consuming the source
        AudioSegments, which is the most robust way to prevent skipping.
        """
        with self._lock:
            if not self.userDict:
                print("No audio sources available, waiting for new sources.")
                return None
            
            # Determine the duration of source audio to read based on pitch
            try:
                new_sample_rate = self.SAMPLING_RATE * (2.0 ** (self.pitch - 1.0))
                speed_multiplier = new_sample_rate / self.SAMPLING_RATE
                source_duration_to_process_ms = (duration_ms * speed_multiplier)
            except (ValueError, ZeroDivisionError):
                print(f"Invalid pitch value {self.pitch}, using default duration.")
                source_duration_to_process_ms = float(duration_ms)
            

            # 1. Mix a batch from all sources.
            mixed_batch = AudioSegment.silent(duration=source_duration_to_process_ms, frame_rate=self.SAMPLING_RATE)
            users_to_remove = set()
            
            # Use a copy of the items to iterate over, allowing safe modification of the dictionary
            for user, data in list(self.userDict.items()):
                segment: AudioSegment = data['segment']

                if len(segment) > 0:
                    # Take a slice from the beginning of the source audio
                    chunk_to_process = segment[:source_duration_to_process_ms]
                    mixed_batch = mixed_batch.overlay(chunk_to_process)
                    
                    # ** THE DEFINITIVE FIX FOR SKIPPING **
                    # Consume the source audio by replacing it with the remainder *immediately*.
                    # This prevents state inconsistencies within the same batch.
                    self.userDict[user]['segment'] = segment[source_duration_to_process_ms:]
                
                # Mark user for removal if their audio is fully consumed
                if len(self.userDict[user]['segment']) == 0:
                    users_to_remove.add(user)
            
            for user in users_to_remove:
                del self.userDict[user]
        
        # 2. Apply effects to the entire batch at once.
        try:
            processed_batch = mixed_batch
            if self.pitch != 1.0:
                pitched_sound = processed_batch._spawn(processed_batch.raw_data, overrides={'frame_rate': math.floor(new_sample_rate)})
                processed_batch = pitched_sound.set_frame_rate(self.SAMPLING_RATE)
            if self.volume != 100:
                if self.volume > 0:
                    gain = 20 * math.log10(self.volume / 100.0)
                    processed_batch = processed_batch.apply_gain(gain)
                else:
                    processed_batch = AudioSegment.silent(duration=len(processed_batch))
            
            return processed_batch
        except Exception as e:
            print(f"Error processing audio batch: {e}")
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

                loops += 1
                opusData = self.encoder.encode(frame_data, self.SAMPLES_PER_FRAME)
                play_audio(opusData, encode=False)

                next_tick = startTimer + (self.DELAY * loops)
                delay = max(0, next_tick - time.perf_counter())
                time.sleep(delay)

            except queue.Empty:
                self.send_silence(1)
                was_idle = True # <--- 3. Set the flag when the queue runs dry
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
        self._sources_exist.set()
        with self._lock:
            self.userDict.clear()
            self.pausedUserDict.clear()
        _log.info("AudioPlayer stop called.")
    
    def stop_user(self, user: str):
        with self._lock:
            self.userDict.pop(user, None)
            self.pausedUserDict.pop(user, None)
            
    def pause_user(self, user: str):
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

        The producer consumes each source segment in place as it mixes, so the
        length of what's left is the playback position.
        """
        with self._lock:
            data = self.userDict.get(user) or self.pausedUserDict.get(user)
            if data is None:
                return None
            return len(data['segment'])

    def change_pitch(self, newPitch: float):
        if not (0.25 <= newPitch <= 4.0):
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

        newSound = newSound.set_frame_rate(48000).set_channels(2).set_sample_width(2)
        newSound = effects.normalize(newSound)

        _log.debug(f"Adding new audio source for user {user} with length {len(newSound)}ms.")
        
        with self._lock:
            self._sources_exist.clear()
            self.userDict[user] = {'segment': newSound}
            self._sources_exist.set()
    
    def set_volume(self, volume: int):
        self.volume = max(0, min(200, volume))

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

    user_channel = inter.user.voice.channel

    # Connect or move to the correct channel
    if guild.voice_client is None:
        await user_channel.connect()
    elif guild.voice_client.channel != user_channel:
        await guild.voice_client.move_to(user_channel)

    if guild not in audioVolume:
        audioVolume[guild] = 20

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
