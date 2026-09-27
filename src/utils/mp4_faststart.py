"""MP4 "faststart" (a.k.a. "Web Optimized"): the index (moov box) at the
start of the file instead of after the video data. Without it, Plex and
other players read through the whole file before starting to play.

ensure_faststart() rewrites a file that needs it by moving the index -
a stream copy with ffmpeg, no re-encoding.
"""
import os
import shutil
import struct

from src.var import print_status
from src.utils.ffmpeg_progress import run_ffmpeg, media_duration


def _top_level_boxes(path, limit=64):
    with open(path, "rb") as f:
        total = os.fstat(f.fileno()).st_size
        pos = 0
        for _ in range(limit):
            if pos + 8 > total:
                return
            f.seek(pos)
            size, kind = struct.unpack(">I4s", f.read(8))
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
            elif size == 0:
                size = total - pos
            if size < 8 or not kind.isalnum():
                return  # not an MP4 box structure (e.g. MPEG-TS renamed .mp4)
            yield kind
            pos += size


def is_faststart(path):
    """True if the index comes before the video data, False if after, None
    when it can't tell (not an MP4, unreadable)."""
    try:
        for kind in _top_level_boxes(path):
            if kind == b"moov":
                return True
            if kind == b"mdat":
                return False
    except (OSError, struct.error):
        pass
    return None


def ensure_faststart(path, quiet=False):
    """Move the index to the start of an .mp4 if it isn't there yet.
    Returns True if the file was rewritten."""
    if not path or not path.lower().endswith((".mp4", ".m4v")) or not os.path.isfile(path):
        return False
    if is_faststart(path) is not False or not shutil.which("ffmpeg"):
        return False
    tmp = path + ".faststart.part"
    if not quiet:
        print_status(f"Faststart (index en début de fichier, sans ré-encodage) : {os.path.basename(path)}", "loading")
    duration = media_duration(path)
    for mapping in (["-map", "0"], []):  # every stream; if that fails, the main ones
        ok = run_ffmpeg(["-i", path, *mapping, "-c", "copy", "-ignore_unknown",
                         "-movflags", "+faststart", "-f", "mp4", tmp], "⚡ Faststart", duration)
        if ok and os.path.exists(tmp) and os.path.getsize(tmp) > 0 and is_faststart(tmp):
            os.replace(tmp, path)
            return True
        try:
            os.remove(tmp)
        except OSError:
            pass
    print_status(f"Impossible d'optimiser {os.path.basename(path)} (fichier laissé tel quel)", "warning")
    return False


def fix_folder(folder):
    """Rewrite every .mp4 under folder that isn't faststart."""
    if not shutil.which("ffmpeg"):
        print_status("ffmpeg est nécessaire pour optimiser les fichiers.", "error")
        return 0
    checked = fixed = 0
    for root, _, files in os.walk(folder):
        for name in sorted(files):
            if name.lower().endswith((".mp4", ".m4v")):
                checked += 1
                if ensure_faststart(os.path.join(root, name)):
                    fixed += 1
    print_status(f"{folder} : {checked} fichier(s) .mp4 vérifié(s), {fixed} optimisé(s).", "success")
    return fixed
