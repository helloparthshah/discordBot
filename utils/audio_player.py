import asyncio
from io import BytesIO
import logging
import threading
import time
from typing import Any, Callable, Optional
import math
import typing

import discord
from discord import Guild, VoiceClient
from discord.enums import SpeakingState
from discord.opus import Encoder as OpusEncoder, OPUS_SILENCE
from pydub import AudioSegment, effects
import pydub
import traceback

_log = logging.getLogger(__name__)


class AudioPlayer(threading.Thread):
    DELAY: float = OpusEncoder.FRAME_LENGTH / 1000.0
    FRAME_SIZE: int = OpusEncoder.FRAME_SIZE

    def __init__(
        self,
        client: VoiceClient,
        encoder: OpusEncoder,
        *,
        after: Optional[Callable[[Optional[Exception]], Any]] = None,
    ) -> None:
        super().__init__(daemon=True, name=f'audio-player:{id(self):#x}')
        self.client: VoiceClient = client
        self.after: Optional[Callable[[Optional[Exception]], Any]] = after

        self._end: threading.Event = threading.Event()
        # self._resumed: threading.Event = threading.Event()
        # self._resumed.set()  # we are not paused
        self._items_in_queue = threading.Event()
        self._items_in_queue.clear()
        self._current_error: Optional[Exception] = None
        self._lock: threading.Lock = threading.Lock()

        if after is not None and not callable(after):
            raise TypeError('Expected a callable for the "after" parameter.')
        
        self.encoder = encoder

        self.userDict = {}

        self.volume:int = 100

    def _do_run(self) -> None:
        # getattr lookup speed ups
        client = self.client
        play_audio = client.send_audio_packet
        self._speak(SpeakingState.voice)

        newLoopStarted = True
        startTimer = None
        loops = None
        while True:
            if self._end.is_set():
                break
            self._items_in_queue.wait()
            if newLoopStarted:
                print("New loop started")
                startTimer = time.perf_counter()
                loops = 0
                newLoopStarted = False
            data = None
            with self._lock:
                # check if userDict is empty
                if len(self.userDict) > 0:
                    data = pydub.AudioSegment.silent(duration=20, frame_rate=OpusEncoder.SAMPLING_RATE)
                    for user in self.userDict.keys():
                        if self.userDict[user] != None:
                            # use overlay to mix the sounds
                            readData = min(self.FRAME_SIZE, len(self.userDict[user]))
                            buffer = BytesIO(self.userDict[user][readData:])
                            buffer.seek(0)
                            data = data.overlay(pydub.AudioSegment.from_raw(buffer, sample_width=2, channels=2, frame_rate=OpusEncoder.SAMPLING_RATE))
                            if (len(self.userDict[user]) >= self.FRAME_SIZE):
                                self.userDict[user] = self.userDict[user][self.FRAME_SIZE:]
                            else:
                                self.userDict[user] = None
                        else:
                            self.userDict[user] = None
                    self.userDict = {key:val for key, val in self.userDict.items() if val != None}
                    if len(self.userDict) == 0:
                        newLoopStarted = True
                        self._items_in_queue.clear()
                else:
                    newLoopStarted = True
                    self._items_in_queue.clear()
                
            # Set volume of output
            data = data.apply_gain(20*math.log10(self.volume/100)) #i forgor if its -20 or -10
            data = bytes(data.raw_data)
            if not data:
                self.send_silence()
                newLoopStarted = True
                continue

            loops += 1

            # are we disconnected from voice?
            if not client.is_connected():
                _log.debug('Not connected, waiting for %ss...', client.timeout)
                # wait until we are connected, but not forever
                connected = client.wait_until_connected(client.timeout)
                if self._end.is_set() or not connected:
                    _log.debug('Aborting playback')
                    return
                _log.debug('Reconnected, resuming playback')
                self._speak(SpeakingState.voice)

            opusData = self.encoder.encode(data, self.encoder.SAMPLES_PER_FRAME)
            play_audio(opusData, encode=False)
            delay = max(0, startTimer + (self.DELAY * loops - time.perf_counter()))
            time.sleep(delay)
            

    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:
            print("Error in run")
            print(traceback.format_exc())
            self._current_error = exc
            self.stop()
        finally:
            #self._call_after()
            #self.source.cleanup()
            pass
        
    def stop(self):
        self._end.set()

    def _speak(self, speaking: SpeakingState) -> None:
        try:
            asyncio.run_coroutine_threadsafe(self.client.ws.speak(speaking), self.client.client.loop)
        except Exception:
            _log.exception("Speaking call in player failed")

    def send_silence(self, count: int = 5) -> None:
        try:
            for n in range(count):
                self.client.send_audio_packet(OPUS_SILENCE, encode=False)
        except Exception:
            # Any possible error (probably a socket error) is so inconsequential it's not even worth logging
            pass
    
    def current_client(self) -> VoiceClient:
        return self.client
    
    def add_to_source_queue(self, newSound: AudioSegment, user: str) -> bool:
        if True: #newSound.frame_rate != 48000 or newSound.channels != 2: #sample rate isnt 48 khz stereo, need to convert
            print("file not in 48khz stereo, converting")
            newSound = newSound.set_sample_width(2).set_channels(2).set_frame_rate(48000)
            buffer = BytesIO()
            newSound.export(buffer, format="s16le", parameters=["-ac", "2", "-ar", "48000"])
            buffer.seek(0)
            newSound = AudioSegment.from_raw(buffer, sample_width=2, channels=2, frame_rate=48000)
            newSound = effects.normalize(newSound)
        print("Waiting to overlay")
        # with self._lock:
        print(self.userDict.keys())
        # userId = round(time.time())
        self.userDict[user] = newSound.raw_data
        # sort it based on length
        self.userDict = dict(sorted(self.userDict.items(), key=lambda item: len(item[1])))
        self._items_in_queue.set()
        return True
    
    def set_volume(self, volume: int):
        self.volume = volume

audioClients: typing.Dict[str, AudioPlayer] = {}
audioVolume = {}
encoder = discord.opus.Encoder(
    application='audio',
    bitrate=128,
    fec=True,
    expected_packet_loss=0.15,
    bandwidth='full',
    signal_type='auto',
)

async def play(inter: discord.Interaction, sound: AudioSegment, identifier):
    guild = inter.guild
    if not inter.user.voice:
        return
    if guild.voice_client == None or guild.voice_client.channel == None:
        await inter.user.voice.channel.connect()
    elif guild.voice_client.channel != inter.user.voice.channel:
        await guild.change_voice_state(channel=inter.user.voice.channel)
    if guild not in audioVolume:
            audioVolume[guild] = 100
    if (guild not in audioClients.keys() or
            audioClients[guild] == None or
            audioClients[guild].current_client() != guild.voice_client):
        audioClients[guild] = AudioPlayer(guild.voice_client, encoder)
        audioClients[guild].start()
        # set volume to already set volume
        set_volume(inter, audioVolume[guild])
    
    audioClients[guild].add_to_source_queue(sound, identifier)
        

def set_volume(inter: discord.Interaction, volume: int):
    guild = inter.guild
    audioVolume[guild] = volume
    if guild in audioClients.keys() and audioClients[guild] != None:
        audioClients[guild].set_volume(volume)