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

        self._end: threading.Event = threading.Event()
        self._resumed: threading.Event = threading.Event()
        self._resumed.set()  # we are not paused
        self._current_error: Optional[Exception] = None
        self._lock: threading.Lock = threading.Lock()

        if after is not None and not callable(after):
            raise TypeError('Expected a callable for the "after" parameter.')
        
        self.encoder = encoder
        self.soundQueue:List[BytesIO] = []

    def _do_run(self) -> None:
        self.loops = 0
        self._start = time.perf_counter()

        # getattr lookup speed ups
        client = self.client
        play_audio = client.send_audio_packet
        self._speak(SpeakingState.voice)

        while client.is_connected():
            startTimer = time.perf_counter()
            data = None
            with self._lock:
                self.soundQueue = [buffer for buffer in self.soundQueue if buffer.tell() != len(buffer.getbuffer()) ]
                for buffer in self.soundQueue:
                    data = buffer.read(3840) #20ms for one frame
                    break
                

            if not data:
                #self.stop()
                self.send_silence()
                time.sleep(1)
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
                # reset our internal data
                self.loops = 0
                self._start = time.perf_counter()

            opusData = data
            opusData = self.encoder.encode(data, self.encoder.SAMPLES_PER_FRAME)
            print(len(data))
            play_audio(opusData, encode=False)
            delay = max(0, self.DELAY - (time.perf_counter() - startTimer)) #need to update for better precision and re-syncing to 20ms segments from loop start, implement after overlay queueing
            time.sleep(0.01)
            

    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:
            self._current_error = exc
            self.stop()
        finally:
            self._call_after()
            #self.source.cleanup()

    def _call_after(self) -> None:
        error = self._current_error

        if self.after is not None:
            try:
                self.after(error)
            except Exception as exc:
                exc.__context__ = error
                _log.exception('Calling the after function failed.', exc_info=exc)
        elif error:
            _log.exception('Exception in voice thread %s', self.name, exc_info=error)

    def stop(self) -> None:
        self._end.set()
        self._resumed.set()
        self._speak(SpeakingState.none)

    def pause(self, *, update_speaking: bool = True) -> None:
        self._resumed.clear()
        if update_speaking:
            self._speak(SpeakingState.none)

    def resume(self, *, update_speaking: bool = True) -> None:
        self.loops: int = 0
        self._start: float = time.perf_counter()
        self._resumed.set()
        if update_speaking:
            self._speak(SpeakingState.voice)

    def is_playing(self) -> bool:
        return self._resumed.is_set() and not self._end.is_set()

    def is_paused(self) -> bool:
        return not self._end.is_set() and not self._resumed.is_set()

    def set_source(self, source: AudioSource) -> None:
        with self._lock:
            self.pause(update_speaking=False)
            self.source = source
            self.resume(update_speaking=False)

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
