"""Anime download parallelism (changed in the Anime/NBA menu's settings).

  * segment_threads(): video pieces fetched at once for one episode - HLS
    segments, or byte ranges of a single-file video (Sibnet, Sendvid...);
  * parallel_episodes(): episodes of a season downloaded at the same time.

Defaults 16 x 2 = 32 connections at most, like the NBA downloader.
"""
from src.utils.config.config import get_setting

DEFAULT_SEGMENT_THREADS = 16
DEFAULT_PARALLEL_EPISODES = 2
# Single-file hosts tolerate fewer simultaneous connections than HLS CDNs.
MAX_RANGE_CONNECTIONS = 8


def _int_setting(key, default):
    try:
        return max(1, int(get_setting(key, default)))
    except (TypeError, ValueError):
        return default


def segment_threads():
    return _int_setting("anime_threads", DEFAULT_SEGMENT_THREADS)


def parallel_episodes():
    return _int_setting("anime_parallel_episodes", DEFAULT_PARALLEL_EPISODES)


def range_connections():
    return min(segment_threads(), MAX_RANGE_CONNECTIONS)


# When the setting is 1 (parallelism off) but parallel downloading was
# still asked for (--fast / --threads, or "y" at the prompt), use the
# downloader's former pool sizes.
def segment_workers():
    n = segment_threads()
    return n if n > 1 else 10


def episode_workers():
    n = parallel_episodes()
    return n if n > 1 else None
