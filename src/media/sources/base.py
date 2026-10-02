"""Data model and the Source base class shared by every Films & Séries
source (src/media/sources/).

A source is anything that can be searched and from which a movie or a
series can be fetched and downloaded. To add one, subclass `Source`,
implement `search()` and `fetch()`, and register it in
src/media/sources/__init__.py - nothing else in the program needs to
change (the menu, the "search everywhere" option and the Plex naming are
all source-agnostic and work off the objects below).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SearchResult:
    """One hit in a source's search results. `id` is whatever the source
    needs to fetch the full item later (an identifier, a URL...)."""
    source: str                     # source key, e.g. "archive"
    source_label: str               # human name, e.g. "Internet Archive"
    id: str
    title: str
    year: Optional[int] = None
    kind: str = "unknown"           # "movie", "series" or "unknown" (known after fetch)
    description: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class VideoFile:
    """One downloadable video file (one quality/format of a movie or an
    episode). A single logical video usually has several of these."""
    url: str
    format: str = ""                # human label, e.g. "h.264" / "Matroska"
    ext: str = "mp4"                # container extension, no dot
    height: Optional[int] = None
    size: Optional[int] = None      # bytes
    referer: Optional[str] = None


@dataclass
class Episode:
    season: int
    number: int
    title: str = ""
    videos: List[VideoFile] = field(default_factory=list)


@dataclass
class MediaItem:
    """A fully resolved item, ready to download: either a movie (use
    `videos`) or a series (use `episodes`)."""
    source: str
    title: str
    year: Optional[int] = None
    kind: str = "movie"             # "movie" or "series"
    videos: List[VideoFile] = field(default_factory=list)
    episodes: List[Episode] = field(default_factory=list)
    page_url: str = ""


def best_video(videos, max_height=None):
    """Pick the best VideoFile from a list: the highest resolution at or
    below max_height (when known), preferring an .mp4 container and, as a
    last tie-break, the larger file. Never returns None for a non-empty
    list - a cap that excludes everything falls back to the best available
    rather than downloading nothing."""
    if not videos:
        return None
    _EXT_RANK = {"mp4": 3, "m4v": 3, "mkv": 2, "webm": 1, "ogv": 1}

    def score(v):
        under = 1 if (not max_height or not v.height or v.height <= max_height) else 0
        return (under, v.height or 0 if under else 0, _EXT_RANK.get(v.ext, 0), v.size or 0)

    return max(videos, key=score)


class Source:
    """Base class for a Films & Séries source. Subclasses set `key` and
    `label` and implement `search` and `fetch`."""
    key = ""
    label = ""

    def search(self, query, limit=20):
        """Return a list[SearchResult] for `query` (most relevant first)."""
        raise NotImplementedError

    def fetch(self, result):
        """Resolve a SearchResult (or a raw id/URL string from a paste)
        into a MediaItem, or None on failure."""
        raise NotImplementedError

    def matches_url(self, text):
        """True if `text` is an id/URL this source can fetch directly
        (used when the user pastes something instead of searching)."""
        return False


_YEAR_RE = re.compile(r"(19|20)\d{2}")


def parse_year(value):
    """A 4-digit year out of an int, a 'YYYY', a 'YYYY-MM-DD' or free text."""
    if value is None:
        return None
    if isinstance(value, int):
        return value if 1870 <= value <= 2100 else None
    m = _YEAR_RE.search(str(value))
    return int(m.group(0)) if m else None
