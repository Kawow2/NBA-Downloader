"""Internet Archive source (archive.org): public-domain and
openly-licensed films and series, through the site's official JSON APIs.

  * search   -> https://archive.org/advancedsearch.php?q=...&output=json
  * metadata -> https://archive.org/metadata/<identifier>
  * download -> https://archive.org/download/<identifier>/<file>

archive.org puts films and TV under one "movies" mediatype; whether an
item is a movie or a series is decided here from its files (one logical
video = a movie, several = a series), not from the mediatype.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlparse

import requests

from src.var import DEFAULT_USER_AGENT, print_status
from src.media.sources.base import (Source, SearchResult, MediaItem, Episode,
                                     VideoFile, parse_year)

_API = "https://archive.org"

# Formats archive.org reports for actual video files (as opposed to
# thumbnails, subtitles, metadata, torrents...). Matched case-insensitively
# as a substring of the file's "format" field.
_VIDEO_FORMAT_HINTS = (
    "mpeg4", "h.264", "h264", "matroska", "ogg video", "webm", "divx",
    "quicktime", "windows media", "mpeg2", "mpeg1", "cinepack", "theora",
    "512kb", "hires", "hi-res", "mp4", "avi", "flash video", "3gp",
)
_VIDEO_EXT = (".mp4", ".mkv", ".avi", ".ogv", ".ogg", ".webm", ".m4v", ".mov",
              ".mpg", ".mpeg", ".wmv", ".flv", ".3gp", ".divx", ".ts")

# Seasonal / episode markers in a file name or title.
_SE_RE = re.compile(r"s(?:eason)?\s*0*(\d{1,2})[\s._-]*e(?:p(?:isode)?)?\s*0*(\d{1,3})", re.IGNORECASE)
_EP_RE = re.compile(r"\b(?:e(?:p(?:isode)?)?|part|pt|chapter)\s*0*(\d{1,3})\b", re.IGNORECASE)
_TRAIL_NUM_RE = re.compile(r"(\d{1,3})\s*$")


def _ext_of(name):
    m = re.search(r"\.([a-z0-9]{2,4})$", name.lower())
    return m.group(1) if m else "mp4"


def _is_video_file(f):
    name = (f.get("name") or "").lower()
    fmt = (f.get("format") or "").lower()
    if name.endswith((".gif", ".jpg", ".jpeg", ".png", ".srt", ".vtt", ".txt",
                       ".xml", ".json", ".torrent", ".sqlite", ".pdf")):
        return False
    if name.endswith(_VIDEO_EXT):
        return True
    return any(h in fmt for h in _VIDEO_FORMAT_HINTS)


def _episode_key(name):
    """Group files that are the same logical video (different formats of
    one episode/movie). archive.org links a derivative to its original via
    the 'original' field; originals key on their own name. The extension is
    dropped so an original .mkv and its derivative .mp4 group together."""
    return re.sub(r"\.[a-z0-9]{2,4}$", "", name.lower())


def _parse_season_episode(text):
    """(season, number) from a file name / title, or (None, None)."""
    if not text:
        return None, None
    m = _SE_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _EP_RE.search(text)
    if m:
        return None, int(m.group(1))
    m = _TRAIL_NUM_RE.search(re.sub(r"\.[a-z0-9]{2,4}$", "", text).strip())
    if m:
        return None, int(m.group(1))
    return None, None


def _natural_key(text):
    """Sort "Episode 2" before "Episode 10" (numeric chunks compare as
    numbers)."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", text or "")]


class InternetArchive(Source):
    key = "archive"
    label = "Internet Archive"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT

    # --------------------------------------------------------------- search
    def search(self, query, limit=20):
        params = {
            # Films and series live under the "movies" mediatype.
            "q": f"({query}) AND mediatype:(movies)",
            "fl[]": ["identifier", "title", "year", "mediatype", "description"],
            "sort[]": ["downloads desc"],
            "rows": limit,
            "page": 1,
            "output": "json",
        }
        try:
            resp = self.session.get(f"{_API}/advancedsearch.php", params=params, timeout=25)
            resp.raise_for_status()
            docs = resp.json().get("response", {}).get("docs", [])
        except (requests.RequestException, ValueError) as e:
            print_status(f"Internet Archive injoignable : {e}", "error")
            return []
        results = []
        for d in docs:
            ident = d.get("identifier")
            if not ident:
                continue
            title = d.get("title") or ident
            if isinstance(title, list):
                title = title[0]
            desc = d.get("description") or ""
            if isinstance(desc, list):
                desc = desc[0]
            results.append(SearchResult(
                source=self.key, source_label=self.label, id=ident,
                title=str(title).strip(), year=parse_year(d.get("year")),
                description=re.sub(r"<[^>]+>", "", str(desc))[:300],
            ))
        return results

    # ---------------------------------------------------------------- fetch
    def matches_url(self, text):
        host = (urlparse(text).hostname or "").lower()
        return "archive.org" in host

    def _identifier(self, text):
        m = re.search(r"archive\.org/(?:details|download|metadata|embed)/([^/?#]+)", text)
        if m:
            return m.group(1)
        # A bare identifier pasted on its own.
        text = text.strip().strip("/")
        return text if text and "/" not in text and " " not in text else None

    def fetch(self, result):
        ident = result.id if isinstance(result, SearchResult) else self._identifier(result)
        if not ident:
            print_status("Identifiant Internet Archive introuvable.", "error")
            return None
        try:
            resp = self.session.get(f"{_API}/metadata/{quote(ident)}", timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            print_status(f"Métadonnées Internet Archive illisibles : {e}", "error")
            return None
        if not data or not data.get("files"):
            print_status(f"Aucun fichier pour « {ident} » (élément supprimé ou restreint ?).", "error")
            return None

        meta = data.get("metadata") or {}
        title = meta.get("title") or ident
        if isinstance(title, list):
            title = title[0]
        year = parse_year(meta.get("year") or meta.get("date") or meta.get("publicdate"))
        page_url = f"{_API}/details/{ident}"

        # Group the video files into logical videos (one per movie/episode).
        groups = {}
        for f in data["files"]:
            if not _is_video_file(f):
                continue
            name = f.get("name")
            if not name:
                continue
            if f.get("source") == "derivative" and f.get("original"):
                key = _episode_key(f["original"])
            else:
                key = _episode_key(name)
            groups.setdefault(key, []).append(f)

        if not groups:
            print_status("Aucun fichier vidéo lisible dans cet élément.", "error")
            return None

        def to_videofile(f):
            name = f["name"]
            return VideoFile(
                url=f"{_API}/download/{quote(ident)}/{quote(name)}",
                format=f.get("format") or "",
                ext=_ext_of(name),
                height=int(f["height"]) if str(f.get("height", "")).isdigit() else None,
                size=int(f["size"]) if str(f.get("size", "")).isdigit() else None,
                referer=page_url,
            )

        if len(groups) == 1:
            videos = [to_videofile(f) for f in next(iter(groups.values()))]
            return MediaItem(source=self.key, title=str(title).strip(), year=year,
                             kind="movie", videos=videos, page_url=page_url)

        # Several videos -> a series. Build one episode per group and label it.
        episodes = []
        for key, files in groups.items():
            # Prefer a human title the item provides for the file.
            ep_title = ""
            for f in files:
                if f.get("title"):
                    ep_title = str(f["title"]).strip()
                    break
            season, number = _parse_season_episode(ep_title or key)
            episodes.append((key, ep_title, season, number, [to_videofile(f) for f in files]))

        # Fill in any missing numbers from the natural ordering of the group
        # names, and default a lone-number series to season 1.
        if any(num is None for *_r, num, _v in episodes):
            episodes.sort(key=lambda e: _natural_key(e[0]))
            episodes = [(k, t, s, (n if n is not None else i), v)
                        for i, (k, t, s, n, v) in enumerate(episodes, 1)]
        # Episode title: only an explicit title the archive provides for the
        # file - not the raw filename, so Plex filenames stay clean.
        result_eps = [Episode(season=(s or 1), number=n, title=(t or "").strip(), videos=v)
                      for k, t, s, n, v in episodes]
        result_eps.sort(key=lambda e: (e.season, e.number))
        return MediaItem(source=self.key, title=str(title).strip(), year=year,
                         kind="series", episodes=result_eps, page_url=page_url)
