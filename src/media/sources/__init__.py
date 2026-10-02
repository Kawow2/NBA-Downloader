"""Registry of Films & Séries sources.

To add a source: write a Source subclass (see base.py and
internet_archive.py) and add its class to _SOURCE_CLASSES below. The menu,
the "search every source" option and the Plex naming pick it up with no
other change.
"""
from src.media.sources.base import (Source, SearchResult, MediaItem, Episode,
                                     VideoFile, best_video)
from src.media.sources.internet_archive import InternetArchive

# Order here is the order shown in the source menu.
_SOURCE_CLASSES = [
    InternetArchive,
]

# One shared instance per source (sessions/caches are reused across calls).
_INSTANCES = {}


def all_sources():
    """Every registered source, as instances."""
    for cls in _SOURCE_CLASSES:
        _INSTANCES.setdefault(cls.key, cls())
    return [_INSTANCES[cls.key] for cls in _SOURCE_CLASSES]


def get_source(key):
    """The source with this key (or None). Matches on the key or, loosely,
    on the label so `--source "internet archive"` also works."""
    key = (key or "").strip().lower()
    for src in all_sources():
        if src.key == key or src.label.lower() == key or key in src.label.lower():
            return src
    return None


def source_for_url(text):
    """The source that recognises a pasted id/URL, or None."""
    for src in all_sources():
        try:
            if src.matches_url(text):
                return src
        except Exception:
            continue
    return None


__all__ = ["Source", "SearchResult", "MediaItem", "Episode", "VideoFile",
           "best_video", "all_sources", "get_source", "source_for_url"]
