"""What to play next, when nobody has queued anything.

YouTube builds a "Mix" for every video — an endless radio station seeded from
it, under the playlist id ``RD<videoId>``. That is the same thing YouTube Music
calls a dynamic playlist, and asking for it is one call to the innertube `next`
endpoint. The mix is *curated*, so it stays on-genre in a way that plain
"related videos" does not: the sidebar recommendations are personalised to the
viewer, and for a signed-out client like this one they wander off into whatever
is trending. So the mix is the real source here and related is only a fallback
for videos that don't have one (live streams, mostly).

pytubefix has no wrapper for this. It does keep an up-to-date client context,
which is the part that actually rots, so borrow that and post the request here —
its InnerTube.next() mutates a module-level context dict, and a `playlistId`
left behind in there would follow every later call in the process.
"""
import copy
import logging
import re
from dataclasses import dataclass

import requests

_log = logging.getLogger(__name__)

NEXT_URL = 'https://www.youtube.com/youtubei/v1/next?prettyPrint=false'
TIMEOUT = 15

# Long entries in a mix are compilations, "full album" uploads and hour-long
# radio rips. Every song is decoded into memory whole, so they're a bad idea
# here regardless of taste.
MAX_LENGTH_SECONDS = 20 * 60

_FALLBACK_CONTEXT = {
    'context': {'client': {'clientName': 'WEB', 'osName': 'Windows',
                           'osVersion': '10.0',
                           'clientVersion': '2.20250122.01.00',
                           'platform': 'DESKTOP'}}
}
_FALLBACK_HEADER = {'User-Agent': 'Mozilla/5.0', 'X-Youtube-Client-Name': '1'}


@dataclass(frozen=True)
class Candidate:
    video_id: str
    title: str
    author: str
    seconds: int

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def _client() -> tuple[dict, dict]:
    """A WEB client context to post. Deep-copied: pytubefix hands out the live
    dict it uses for its own requests."""
    try:
        from pytubefix.innertube import _default_clients
        web = _default_clients['WEB']
        return copy.deepcopy(web['innertube_context']), dict(web['header'])
    except Exception:  # pragma: no cover - only if pytubefix restructures
        _log.debug("Falling back to a pinned innertube client", exc_info=True)
        return copy.deepcopy(_FALLBACK_CONTEXT), dict(_FALLBACK_HEADER)


def _watch_next(video_id: str) -> dict:
    payload, header = _client()
    payload.update({'videoId': video_id,
                    'playlistId': f"RD{video_id}",   # the mix seeded from it
                    'contentCheckOk': "true"})
    header['Content-Type'] = 'application/json'
    response = requests.post(NEXT_URL, headers=header, json=payload, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()
    return data.get('contents', {}).get('twoColumnWatchNextResults', {}) or {}


def parse_length(text) -> int:
    """'3:34' or '1:02:11' as seconds. 0 when there's no duration — a live
    stream, which has no end and can't be downloaded."""
    if not text or not re.fullmatch(r'[\d:]+', text):
        return 0
    seconds = 0
    for part in text.split(':'):
        seconds = seconds * 60 + int(part or 0)
    return seconds


def _text(node) -> str:
    """Pull the string out of whichever text wrapper YouTube used."""
    if not isinstance(node, dict):
        return ""
    if 'simpleText' in node:
        return node['simpleText'] or ""
    if 'content' in node:
        return node['content'] or ""
    runs = node.get('runs') or []
    return "".join(run.get('text', '') for run in runs)


def _from_mix(top: dict) -> list[Candidate]:
    playlist = (top.get('playlist') or {}).get('playlist') or {}
    found = []
    for item in playlist.get('contents') or []:
        entry = item.get('playlistPanelVideoRenderer')
        if not entry or not entry.get('videoId'):
            continue
        if entry.get('unplayableText'):
            continue
        found.append(Candidate(
            video_id=entry['videoId'],
            title=_text(entry.get('title')) or "Unknown",
            author=_text(entry.get('shortBylineText')) or "Unknown",
            seconds=parse_length(_text(entry.get('lengthText')))))
    return found


def _badge_length(lockup: dict) -> int:
    thumbnail = (lockup.get('contentImage') or {}).get('thumbnailViewModel') or {}
    for overlay in thumbnail.get('overlays') or []:
        bottom = overlay.get('thumbnailBottomOverlayViewModel') or {}
        for badge in bottom.get('badges') or []:
            length = parse_length((badge.get('thumbnailBadgeViewModel') or {}).get('text'))
            if length:
                return length
    return 0


def _from_related(top: dict) -> list[Candidate]:
    """The sidebar. Two layouts in the wild: lockupViewModel is what YouTube
    serves now, compactVideoRenderer is the older shape it still falls back to."""
    results = ((top.get('secondaryResults') or {})
               .get('secondaryResults') or {}).get('results') or []
    found = []
    for item in results:
        lockup = item.get('lockupViewModel')
        if lockup:
            if lockup.get('contentType') != 'LOCKUP_CONTENT_TYPE_VIDEO':
                continue  # playlists, channels, Shorts shelves
            meta = (lockup.get('metadata') or {}).get('lockupMetadataViewModel') or {}
            rows = (((meta.get('metadata') or {})
                     .get('contentMetadataViewModel') or {}).get('metadataRows') or [])
            parts = rows[0].get('metadataParts') if rows else None
            author = _text((parts or [{}])[0].get('text')) if parts else ""
            found.append(Candidate(
                video_id=lockup.get('contentId') or "",
                title=_text(meta.get('title')) or "Unknown",
                author=author or "Unknown",
                seconds=_badge_length(lockup)))
            continue

        compact = item.get('compactVideoRenderer')
        if compact and compact.get('videoId'):
            found.append(Candidate(
                video_id=compact['videoId'],
                title=_text(compact.get('title')) or "Unknown",
                author=_text(compact.get('longBylineText')) or "Unknown",
                seconds=parse_length(_text(compact.get('lengthText')))))
    return [c for c in found if c.video_id]


def similar_videos(video_id: str) -> list[Candidate]:
    """Songs to follow `video_id`, best first. Network-bound: call off the loop."""
    try:
        top = _watch_next(video_id)
    except Exception:
        _log.warning("Couldn't reach YouTube for a mix on %s", video_id, exc_info=True)
        return []

    found = _from_mix(top) or _from_related(top)
    return [c for c in found if 0 < c.seconds <= MAX_LENGTH_SECONDS]


def pick_next(video_id: str, exclude: set[str]) -> list[Candidate]:
    """Mix candidates that haven't been played yet, best first.

    Returns the whole shortlist rather than one pick: a candidate can still turn
    out to be undownloadable (age-gated, region-locked, deleted), and the caller
    needs something to fall through to.
    """
    return [c for c in similar_videos(video_id)
            if c.video_id not in exclude and c.video_id != video_id]
