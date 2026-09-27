"""Download one direct video file (a plain .mp4 link, not HLS) over many
connections at once, one byte range each - like a download manager.

Video hosts (OK.ru, Filemoon, ...) cap every connection at a few MB/s:
one connection gives ~6-7 MiB/s whatever the line, sixteen add up.
"""
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from tqdm import tqdm

from src.var import print_status

PIECE = 8 * 1024 * 1024
MAX_CONNECTIONS = 16

_local = threading.local()


def _session():
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def _probe(url, headers):
    """(final url, size) if the server serves byte ranges, else None."""
    try:
        with requests.get(url, headers={**headers, "Range": "bytes=0-0"}, stream=True, timeout=20) as r:
            match = re.match(r"bytes 0-0/(\d+)", r.headers.get("Content-Range", ""))
            if r.status_code == 206 and match:
                return r.url, int(match.group(1))
    except requests.RequestException:
        pass
    return None


def download(url, path, headers, connections=MAX_CONNECTIONS, label=None):
    """True once path holds the whole file. False when the server doesn't
    accept ranges or keeps refusing them: the caller then downloads the
    usual way (one connection)."""
    found = _probe(url, headers)
    if not found:
        return False
    url, total = found
    if total < 2 * PIECE:
        return False
    connections = max(1, min(connections, MAX_CONNECTIONS, total // PIECE))
    pieces = [(start, min(start + PIECE, total) - 1) for start in range(0, total, PIECE)]
    failed = threading.Event()
    refused = threading.Event()
    print_status(f"Fichier direct ({total / 1024 ** 3:.2f} Go) : téléchargement sur {connections} connexions", "loading")

    with open(path, "wb") as f:
        f.truncate(total)
    bar = tqdm(total=total, unit="B", unit_scale=True, unit_divisor=1024, desc=label or "📥 Téléchargement",
               bar_format="{desc} {percentage:3.0f}% |{bar:30}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")

    def fetch(piece):
        start, end = piece
        for attempt in range(6):
            if failed.is_set():
                return
            got = 0
            try:
                with _session().get(url, headers={**headers, "Range": f"bytes={start}-{end}"},
                                    stream=True, timeout=30) as r:
                    if r.status_code != 206:
                        if r.status_code in (403, 429) and attempt < 2:
                            time.sleep(2 + attempt * 3)  # too many connections: slow down, retry
                            continue
                        refused.set()
                        failed.set()
                        return
                    with open(path, "r+b") as out:
                        out.seek(start)
                        for chunk in r.iter_content(chunk_size=512 * 1024):
                            out.write(chunk)
                            got += len(chunk)
                            bar.update(len(chunk))
                if got == end - start + 1:
                    return
            except (requests.RequestException, OSError):
                pass
            bar.update(-got)
            time.sleep(min(2 ** attempt, 20))
        failed.set()

    try:
        with ThreadPoolExecutor(max_workers=connections) as pool:
            list(pool.map(fetch, pieces))
    except KeyboardInterrupt:
        failed.set()
        raise
    finally:
        bar.close()
    if failed.is_set() or os.path.getsize(path) != total:
        why = "l'hébergeur refuse les connexions multiples" if refused.is_set() else "morceaux en échec"
        print_status(f"Téléchargement multi-connexions interrompu ({why}) : méthode classique.", "warning")
        try:
            os.remove(path)
        except OSError:
            pass
        return False
    return True
