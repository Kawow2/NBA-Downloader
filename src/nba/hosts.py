"""Built-in extractors for the video hosts basketball-video.com uses most
(OK.ru, Dailymotion), used when yt-dlp fails on them, plus a runtime fix
for a yt-dlp OK.ru bug.

Each extractor returns (stream_url, referer) for ffmpeg, or None.
"""
import html
import json
import re
from urllib.parse import parse_qs, unquote, urlparse

import requests

from src.var import DEFAULT_USER_AGENT, print_status


def patch_ytdlp():
    """yt-dlp's OK.ru extractor json-decodes flashvars["metadata"], which
    OK.ru now sends already decoded ("the JSON object must be str, bytes or
    bytearray, not dict"). Let an already-decoded object pass through."""
    try:
        from yt_dlp.extractor.odnoklassniki import OdnoklassnikiIE
    except Exception:
        return
    if getattr(OdnoklassnikiIE, "_nba_patched", False):
        return
    original = OdnoklassnikiIE._parse_json

    def _parse_json(self, json_string, *args, **kwargs):
        if isinstance(json_string, (dict, list)):
            return json_string
        return original(self, json_string, *args, **kwargs)

    OdnoklassnikiIE._parse_json = _parse_json
    OdnoklassnikiIE._nba_patched = True


def _session():
    """Browser-like HTTP client: curl_cffi impersonation when available
    (Dailymotion rejects plain clients), requests otherwise."""
    try:
        from curl_cffi import requests as creq
        return creq.Session(impersonate="chrome"), True
    except Exception:
        s = requests.Session()
        s.headers["User-Agent"] = DEFAULT_USER_AGENT
        return s, False


# ------------------------------------------------------------------ OK.ru
_OK_QUALITY_ORDER = ("ultra", "quad", "full", "hd", "sd", "low", "lowest", "mobile")
_OK_QUALITY_HEIGHT = {"ultra": 2160, "quad": 1440, "full": 1080, "hd": 720, "sd": 480, "low": 360, "lowest": 240, "mobile": 144}


def okru_stream(url, referer=None, max_height=None):
    m = re.search(r"ok\.ru/(?:videoembed|video|live)/(\d+)", url)
    if not m:
        return None
    embed = f"https://ok.ru/videoembed/{m.group(1)}"
    s, _ = _session()
    try:
        page = s.get(embed, headers={"Referer": referer or "https://ok.ru/"}, timeout=20).text
    except Exception as e:
        print_status(f"OK.ru inaccessible : {e}", "error")
        return None
    opts = re.search(r'data-options="([^"]+)"', page) or re.search(r"data-options='([^']+)'", page)
    if not opts:
        reason = re.search(r'class="[^"]*vp_video_stub_txt[^"]*"[^>]*>([^<]+)', page)
        print_status(f"OK.ru : pas de lecteur ({reason.group(1).strip() if reason else 'vidéo supprimée ou privée ?'})", "error")
        return None
    try:
        options = json.loads(html.unescape(opts.group(1)))
        flashvars = options.get("flashvars") or {}
        metadata = flashvars.get("metadata")
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        if not metadata and flashvars.get("metadataUrl"):
            metadata = s.post(unquote(flashvars["metadataUrl"]), data={"st.location": flashvars.get("location", "")},
                              headers={"Referer": embed}, timeout=20).json()
    except Exception as e:
        print_status(f"OK.ru : métadonnées illisibles ({e})", "error")
        return None
    metadata = metadata or {}
    videos = {v.get("name"): v.get("url") for v in metadata.get("videos") or [] if v.get("url")}
    if max_height:
        # Direct file in the best quality under the cap (the HLS master
        # would make ffmpeg pick the top quality).
        for q in _OK_QUALITY_ORDER:
            if videos.get(q) and _OK_QUALITY_HEIGHT.get(q, 0) <= max_height:
                return videos[q], "https://ok.ru/"
    for key in ("hlsManifestUrl", "ondemandHls", "hlsMasterPlaylistUrl"):
        if metadata.get(key):
            return metadata[key], "https://ok.ru/"
    for q in _OK_QUALITY_ORDER:
        if videos.get(q):
            return videos[q], "https://ok.ru/"
    print_status("OK.ru : aucun flux vidéo dans les métadonnées.", "error")
    return None


# ------------------------------------------------------------- Dailymotion
def _dailymotion_id(url):
    p = urlparse(url)
    vid = (parse_qs(p.query).get("video") or [None])[0]
    if vid:
        return vid
    m = re.search(r"(?:/video/|/embed/video/|dai\.ly/)([a-z0-9]+)", url)
    return m.group(1) if m else None


def dailymotion_stream(url, referer=None):
    vid = _dailymotion_id(url)
    if not vid:
        return None
    s, impersonating = _session()
    params = {"embedder": referer or "https://geo.dailymotion.com/", "locale": "fr-FR"}
    try:
        data = s.get(f"https://www.dailymotion.com/player/metadata/video/{vid}", params=params,
                     headers={"Referer": "https://geo.dailymotion.com/", "Origin": "https://geo.dailymotion.com"},
                     timeout=20).json()
    except Exception as e:
        print_status(f"Dailymotion : métadonnées inaccessibles ({e})", "error")
        return None
    error = data.get("error")
    if error:
        # The real reason ("private video", "not available in your country",
        # "embedding not allowed"...), which yt-dlp reduces to "No video
        # formats found".
        reason = error.get("title") or error.get("message") or error.get("raw_message") or str(error)
        print_status(f"Dailymotion refuse la vidéo {vid} : {reason}", "error")
        return None
    qualities = data.get("qualities") or {}
    for key in ["auto"] + sorted((k for k in qualities if k.isdigit()), key=int, reverse=True):
        for q in qualities.get(key) or []:
            if q.get("url"):
                return q["url"], "https://geo.dailymotion.com/"
    print_status(f"Dailymotion : aucun flux pour {vid}"
                 + ("" if impersonating else " (curl_cffi absent : pip install curl_cffi)"), "error")
    return None


def builtin_stream(url, referer=None, max_height=None):
    host = (urlparse(url).hostname or "").lower()
    if "ok.ru" in host or "odnoklassniki" in host:
        return okru_stream(url, referer, max_height)
    if "dailymotion" in host or host == "dai.ly":
        return dailymotion_stream(url, referer)
    return None


def has_builtin(url):
    host = (urlparse(url).hostname or "").lower()
    return any(h in host for h in ("ok.ru", "odnoklassniki", "dailymotion", "dai.ly"))
