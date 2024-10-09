import asyncio
from io import BytesIO
import logging
import threading
import time
from typing import Any, Callable, Generic, IO, List, Optional, TYPE_CHECKING, Tuple, TypeVar, Union

from discord import AudioSource, VoiceClient
from discord.enums import SpeakingState
from discord.errors import ClientException
from discord.opus import Encoder as OpusEncoder, OPUS_SILENCE
from discord.oggparse import OggStream
from discord.utils import MISSING

_log = logging.getLogger(__name__)


class AudioPlayer(threading.Thread):
    DELAY: float = OpusEncoder.FRAME_LENGTH / 1000.0

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

        # self._end: threading.Event = threading.Event()
        # self._resumed: threading.Event = threading.Event()
        # self._resumed.set()  # we are not paused
        self._items_in_queue = threading.Event()
        self._items_in_queue.clear()
        self._current_error: Optional[Exception] = None
        self._lock: threading.Lock = threading.Lock()

        if after is not None and not callable(after):
            raise TypeError('Expected a callable for the "after" parameter.')
        
        self.encoder = encoder
        self.soundQueue:List[BytesIO] = []

    def _do_run(self) -> None:
        # getattr lookup speed ups
        client = self.client
        play_audio = client.send_audio_packet
        self._speak(SpeakingState.voice)

        newLoopStarted = True
        startTimer = None
        loops = None
        while True:
            self._items_in_queue.wait()
            if newLoopStarted:
                startTimer = time.perf_counter()
                loops = 0
                newLoopStarted = False
            data = None
            with self._lock:
                self.soundQueue = [buffer for buffer in self.soundQueue if buffer.tell() != len(buffer.getbuffer()) ]
                for buffer in self.soundQueue:
                    data = buffer.read(3840) #20ms, 16 bit, 48khz for one frame
                    break
                

            if not data:
                self.send_silence()
                self._items_in_queue.clear()
                newLoopStarted = True
                continue

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

            opusData = data
            opusData = self.encoder.encode(data, self.encoder.SAMPLES_PER_FRAME)
            print(len(data))
            play_audio(opusData, encode=False)
            next_time = startTimer + self.DELAY * loops
            delay = max(0, self.DELAY + (next_time - time.perf_counter()))
            time.sleep(delay)
            

    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:
            self._current_error = exc
            self.stop()
        finally:
            #self._call_after()
            #self.source.cleanup()
            pass

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
    
    def add_to_source_queue(self, buffer: BytesIO):
        with self._lock:
            self.soundQueue.append(buffer)
        self._items_in_queue.set()
