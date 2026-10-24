"""Spoiler-free Plex thumbnails for NBA games.

Without an image of its own, Plex makes an episode's thumbnail from a frame
it picks inside the video - for a game replay often late in the game, with
the scoreboard (the result) in plain sight on the episode list, before
playing anything. Plex uses an image next to the video with the same name
instead (local media assets), so every game gets one taken from its first
minute, before anything is decided:

    Season 2026/NBA - 2026-06-13 - Knicks vs Spurs.mp4
    Season 2026/NBA - 2026-06-13 - Knicks vs Spurs.jpg

A game kept in parts (pt1, pt2...) gets the same image for every part,
taken from the earliest one: a frame from the start of part 2 would show
the halftime score.
"""
import os
import re
import subprocess

from src.var import print_status
from src.utils.check.check_ffmpeg_installed import check_ffmpeg_installed
from src.utils.ffmpeg_progress import media_duration

VIDEO_EXT = (".mp4", ".mkv", ".avi", ".webm", ".mov", ".m4v", ".ts")
_PART = re.compile(r" - pt(\d+)$", re.IGNORECASE)


def thumb_path(video_path):
    return os.path.splitext(video_path)[0] + ".jpg"


def _part_number(path):
    """N for "<game> [server] - ptN.mp4", 0 for a whole game."""
    m = _PART.search(os.path.splitext(os.path.basename(path))[0])
    return int(m.group(1)) if m else 0


def _frame_time(duration):
    # One minute in: past the broadcast intro, long before anything is
    # decided. A short video (a clip): 10 % in.
    if not duration or duration >= 300:
        return 60.0
    return max(1.0, duration * 0.1)


def make_thumbnail(video_path, source=None, force=False):
    """Write <video>.jpg from a frame near the start of `source` (default:
    the video itself). True if the image exists afterwards."""
    out = thumb_path(video_path)
    if os.path.exists(out) and not force:
        return True
    if not check_ffmpeg_installed():
        return False
    source = source or video_path
    tmp = os.path.splitext(video_path)[0] + ".thumb-tmp.jpg"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{_frame_time(media_duration(source)):.2f}", "-i", source,
           # "thumbnail": the most representative of the next 150 frames
           # (~5 s) rather than the first one, which can be black or blurred.
           "-vf", "thumbnail=150,scale='min(1280,iw)':-2",
           "-frames:v", "1", "-update", "1", "-q:v", "2", tmp]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    os.replace(tmp, out)
    return True


def add_game_thumbnails(files):
    """Thumbnails for the files of one game (the whole game, or its parts),
    all taken from the start of the earliest part."""
    files = [f for f in files if f and os.path.exists(f)]
    if not files or all(os.path.exists(thumb_path(f)) for f in files):
        return
    if not check_ffmpeg_installed():
        print_status("ffmpeg absent : pas de miniature sans spoiler, Plex choisira la sienne "
                     "(qui peut montrer le score).", "warning")
        return
    source = min(files, key=_part_number)
    made = [f for f in files if make_thumbnail(f, source=source)]
    if made:
        print_status(f"Miniature Plex sans spoiler : {os.path.basename(thumb_path(made[0]))}", "success")
    else:
        print_status("Miniature sans spoiler impossible à créer (ffmpeg n'a pas pu lire la vidéo).", "warning")


def _is_game_file(name):
    low = name.lower()
    return low.endswith(VIDEO_EXT) and ".ytdlp." not in low and ".part" not in low and ".thumb-tmp" not in low


def fix_folder(folder):
    """Spoiler-free thumbnails for games already downloaded under folder
    (recursively): only for videos that don't have their image yet."""
    if not check_ffmpeg_installed():
        print_status("ffmpeg est nécessaire pour créer les miniatures.", "error")
        return
    if not os.path.isdir(folder):
        print_status(f"Dossier introuvable : {folder}", "error")
        return
    # Group the parts of a game ("<game> [server] - ptN") so they all share
    # the image of the earliest part.
    games = {}
    for root, _, names in os.walk(folder):
        for name in names:
            if not _is_game_file(name):
                continue
            path = os.path.join(root, name)
            key = _PART.sub("", os.path.splitext(path)[0])
            games.setdefault(key, []).append(path)
    todo = {k: v for k, v in games.items() if any(not os.path.exists(thumb_path(f)) for f in v)}
    if not todo:
        print_status(f"Tous les matchs de {folder} ont déjà leur miniature.", "success")
        return
    print_status(f"{len(todo)} match(s) sans miniature dans {folder} : création...", "loading")
    made = failed = 0
    for files in todo.values():
        source = min(files, key=_part_number)
        for f in files:
            if os.path.exists(thumb_path(f)):
                continue
            if make_thumbnail(f, source=source):
                made += 1
            else:
                failed += 1
                print_status(f"Échec : {f}", "warning")
    print_status(f"{made} miniature(s) créée(s)" + (f", {failed} échec(s)" if failed else "") + ".",
                 "success" if not failed else "warning")
    print_status("Dans Plex : « Actualiser les métadonnées » sur la série NBA pour remplacer les anciennes "
                 "miniatures.", "info")
