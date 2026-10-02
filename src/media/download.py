"""Download one video file (a movie or an episode) to disk.

Source files are plain HTTP files (e.g. archive.org's direct download
links), so they go over many byte-range connections at once - the same
multi-connection downloader the NBA side uses (src/nba/fast_http.py) -
falling back to a single streamed connection when the server refuses
ranges. .mp4 files are then made faststart so Plex starts playing at once.
"""
import os

import requests
from tqdm import tqdm

from src.var import DEFAULT_USER_AGENT, print_status
from src.nba import fast_http
from src.utils.download.verify_video_file import verify_video_file
from src.utils.mp4_faststart import ensure_faststart


def _cleanup(*paths):
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def _plain_download(url, path, headers):
    """One connection, for a server that refuses byte ranges."""
    try:
        with requests.get(url, headers=headers, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0) or None
            bar = tqdm(total=total, unit="B", unit_scale=True, unit_divisor=1024,
                       desc="📥 Téléchargement",
                       bar_format="{desc} {percentage:3.0f}% |{bar:30}| {n_fmt}/{total_fmt} "
                       "[{elapsed}<{remaining}, {rate_fmt}]" if total else "{desc} {n_fmt} [{elapsed}]")
            try:
                with open(path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=512 * 1024):
                        f.write(chunk)
                        bar.update(len(chunk))
            finally:
                bar.close()
        return True
    except (requests.RequestException, OSError) as e:
        print_status(f"Téléchargement impossible : {e}", "error")
        _cleanup(path)
        return False


def download_video_file(video, out_path, threads=16):
    """Download a VideoFile to out_path (.part while in progress). Returns
    True once the file is complete and passes a sanity check."""
    try:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    except OSError as e:
        print_status(f"Impossible de créer le dossier : {e.strerror or e}", "error")
        return False
    if os.path.exists(out_path):
        print_status(f"Déjà présent : {out_path}", "success")
        return True

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    if video.referer:
        headers["Referer"] = video.referer
    tmp = out_path + ".part"
    label = "📥 " + os.path.basename(out_path)

    ok = fast_http.download(video.url, tmp, headers, threads, label=label)
    if not ok:
        ok = _plain_download(video.url, tmp, headers)
    if not ok or not os.path.exists(tmp):
        _cleanup(tmp)
        return False

    valid, reason = verify_video_file(tmp)
    if not valid:
        print_status(f"Fichier invalide ({reason}) : abandon.", "error")
        _cleanup(tmp)
        return False

    os.replace(tmp, out_path)
    if out_path.lower().endswith(".mp4"):
        ensure_faststart(out_path)
    return True
