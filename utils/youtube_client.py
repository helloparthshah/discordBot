"""Getting a handle on a YouTube video we can actually download.

pytubefix reaches YouTube by impersonating one of its official clients, and
YouTube gates those clients intermittently — the same video, on the same
client, answers "Sign in to confirm you're not a bot" one minute and hands over
the audio the next. Measured over repeated attempts on a video that had just
failed in production, ANDROID_VR returned the audio three times in five and
raised BotDetection the other two.

Intermittent, not per-video, is the important part: it means retrying is worth
as much as switching clients. So do both — walk the client list, twice.

ANDROID_VR goes first: it needs no proof-of-origin token and answers in well
under a second. MWEB is the fallback at roughly three, because pytubefix has to
run YouTube's botGuard script through Node to mint a token first, but it is
gated separately from ANDROID_VR — when one is refused the other usually isn't.

The clients left out were measured, not guessed. WEB, WEB_SAFARI, TV and
ANDROID_TESTSUITE all return SABR streams, which can't be fetched without a
proof-of-origin token; the ANDROID_*/IOS_* family answers LoginRequired or a
bare HTTP 400.
"""
import logging
import time

from pytubefix import YouTube, exceptions

_log = logging.getLogger(__name__)

CLIENTS = ('ANDROID_VR', 'MWEB')
# Passes over the client list. Four attempts total, and since the gate is
# intermittent each one is a fresh roll rather than a repeat of the last.
ROUNDS = 2
RETRY_DELAY = 1.0

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
    """The downloadable audio stream, or None if this client can't give us one."""
    stream = yt.streams.get_audio_only()
    if stream is None:
        return None
    if getattr(stream, 'is_sabr', False):
        # Server-side adaptive. pytubefix can only pull these with a
        # proof-of-origin token; without one the download dies part way through
        # with "PoToken PENDING_MISSING", which is worse than not starting.
        return None
    return stream


def open_video(url: str) -> YouTube:
    """A YouTube handle whose audio can be downloaded.

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
            if audio_stream(yt) is not None:
                if attempt:
                    _log.info("Opened %s with %s on attempt %d",
                              url, client, attempt + 1)
                return yt
            last_error = NoPlayableStream(
                f"{client} had no audio we can download")
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
