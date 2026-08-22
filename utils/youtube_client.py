"""Getting a handle on a YouTube video we can actually download.

pytubefix reaches YouTube by impersonating one of its official clients, and
which of them will serve a given video changes by the video and by the minute.
There are two separate ways a client can let us down, and only one of them
looks like a failure:

  * it refuses outright — "Sign in to confirm you're not a bot", a login
    demand, a bare HTTP 400. Measured over repeated attempts, ANDROID_VR
    returned the audio three times in five on a video that had just failed in
    production, so retrying is worth as much as switching clients.

  * it hands over a URL that only plays the first megabyte. Everything looks
    healthy — the video opens, the title is right, the stream lists a bitrate —
    and then the download dies a few seconds in with "HTTP Error 403:
    Forbidden". MWEB does this to some videos; measured on one of them, bytes
    0-1048575 came back 206 and every byte after that came back 403, whether
    asked for in one request or in twenty.

So don't believe a client until its URL has served a byte past that line. That
is what `playable` is for, and it is what makes walking the client list work at
all: a gated client now gets skipped instead of being played and failing.

The clients here were measured, not guessed. ANDROID_VR goes first because it
needs no proof-of-origin token and answers in under half a second. WEB_MUSIC is
the workhorse behind it — around five seconds, since pytubefix has to run
YouTube's botGuard script through Node to mint a token first, but it served
every video ANDROID_VR refused. MWEB is last because it is the one that gates,
though on plenty of videos it is fine.

The ones left out were measured too: WEB and WEB_SAFARI return SABR streams,
which can't be fetched without a token at all; TV and the ANDROID_*/IOS_*
families answer LoginRequired, "unavailable", or HTTP 400.
"""
import logging
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pytubefix import YouTube, exceptions

_log = logging.getLogger(__name__)

CLIENTS = ('ANDROID_VR', 'WEB_MUSIC', 'MWEB')
# Passes over the client list. Refusals are intermittent, so a second pass is a
# fresh roll rather than a repeat of the last.
ROUNDS = 2
RETRY_DELAY = 1.0

# How much a gated URL will serve before it starts refusing. Asking for a byte
# on the far side of this is what separates a real stream from a teaser.
TEASER_LIMIT = 1 << 20
PROBE_BYTES = 1024
PROBE_TIMEOUT = 10
# What pytubefix sends when it fetches media; keep the probe indistinguishable
# from the download it stands in for.
_HEADERS = {'User-Agent': 'Mozilla/5.0', 'accept-language': 'en-US,en'}

# These say something about the video rather than about how we asked for it.
# Another client gets the same answer, so don't spend the round trips.
TERMINAL = (exceptions.VideoPrivate,
            exceptions.MembersOnly,
            exceptions.VideoRegionBlocked,
            exceptions.RecordingUnavailable,
            exceptions.LiveStreamError,
            exceptions.AgeRestrictedError,
            exceptions.AgeCheckRequiredError,
            exceptions.AgeCheckRequiredAccountError)


class NoPlayableStream(Exception):
    """Every client we're willing to use came back empty."""


def audio_stream(yt: YouTube):
    """The audio stream of the right shape, or None. Makes no request."""
    stream = yt.streams.get_audio_only()
    if stream is None:
        return None
    if getattr(stream, 'is_sabr', False):
        # Server-side adaptive. pytubefix can only pull these with a
        # proof-of-origin token; without one the download dies part way through
        # with "PoToken PENDING_MISSING", which is worse than not starting.
        return None
    return stream


def playable(stream) -> bool:
    """Will this URL serve the whole file, or only the first megabyte?

    One ranged request for a kilobyte from just past the teaser gate. A stream
    shorter than the gate answers 416, which is a pass — nothing can be
    withheld from us there.
    """
    request = Request(stream.url, headers=dict(
        _HEADERS,
        Range=f'bytes={TEASER_LIMIT}-{TEASER_LIMIT + PROBE_BYTES - 1}'))
    try:
        with urlopen(request, timeout=PROBE_TIMEOUT) as response:
            return bool(response.read(1))
    except HTTPError as error:
        if error.code == 416:
            return True
        _log.debug("Stream is gated: HTTP %s past %d bytes",
                   error.code, TEASER_LIMIT)
        return False
    except Exception as error:
        _log.debug("Couldn't probe the stream: %s", error)
        return False


def open_video(url: str) -> YouTube:
    """A YouTube handle whose audio can be downloaded in full.

    Blocking, and deliberately patient — several round trips and a pause
    between attempts. Call it in a thread.
    """
    attempts = [client for _ in range(ROUNDS) for client in CLIENTS]
    last_error = None

    for attempt, client in enumerate(attempts):
        if attempt:
            time.sleep(RETRY_DELAY)
        try:
            yt = YouTube(url, client=client)
            stream = audio_stream(yt)
            if stream is None:
                last_error = NoPlayableStream(
                    f"{client} offered no plain audio")
            elif not playable(stream):
                last_error = NoPlayableStream(
                    f"{client} offered a stream that stops after "
                    f"{TEASER_LIMIT // 1024} KiB")
            else:
                if attempt:
                    _log.info("Opened %s with %s on attempt %d",
                              url, client, attempt + 1)
                return yt
            _log.debug("%s is no good for %s: %s", client, url, last_error)
        except TERMINAL:
            raise
        except Exception as exc:
            last_error = exc
            _log.debug("%s refused %s: %s", client, url, exc)

    _log.warning("Gave up on %s after %d attempts: %s",
                 url, len(attempts), last_error)
    if isinstance(last_error, exceptions.RegexMatchError):
        # Failing to find a function inside base.js means the installed
        # pytubefix predates YouTube's current player, not that the video is
        # unavailable. Say so — the raw error reads like a YouTube problem.
        _log.warning("That last error means pytubefix can't read YouTube's "
                     "current player. Run: pip install -r requirements.txt")
    raise last_error or NoPlayableStream(url)


def fetch_audio(yt: YouTube, path: str) -> YouTube:
    """Download `yt`'s audio to `path`. Blocking; call it in a thread.

    Returns the handle the bytes actually came from, which may not be the one
    passed in: a song can sit in the queue for an hour before it plays and a
    stream URL is signed and short-lived, so a refused download is worth one
    more walk through the clients before calling the video dead.

    Leaves no half-written file behind on failure.
    """
    try:
        _download(yt, path)
        return yt
    except Exception as exc:
        _log.info("Reopening %s, its stream wouldn't download: %s",
                  yt.watch_url, exc)
        _discard(path)

    fresh = open_video(yt.watch_url)
    try:
        _download(fresh, path)
    except Exception:
        _discard(path)
        raise
    return fresh


def _download(yt: YouTube, path: str) -> None:
    stream = audio_stream(yt)
    if stream is None:
        raise NoPlayableStream(yt.watch_url)
    directory, filename = os.path.split(path)
    # skip_existing would happily hand back a truncated leftover from a
    # download that died, and a truncated m4a decodes to a truncated song.
    stream.download(output_path=directory or '.', filename=filename,
                    skip_existing=False)


def _discard(path: str) -> None:
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            _log.debug("Couldn't clear %s", path)
