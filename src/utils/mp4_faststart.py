"""MP4 "faststart" (a.k.a. "Web Optimized"): the index (moov box) at the
start of the file instead of after the video data. Without it, Plex and
other players read through the whole file before starting to play.

ensure_faststart() rewrites a file that needs it by moving the index -
a stream copy with ffmpeg, no re-encoding - and first repairs a sound the
file describes wrongly (see repair_audio.py).
"""
import os
import shutil
import struct
import subprocess

from src.var import print_status
from src.utils.ffmpeg_progress import run_ffmpeg, media_duration
from src.utils.repair_audio import _RATES, real_sample_rate, repair_audio


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


def _audio_signature(path):
    """(sample rate, channels) of the first audio track as decoded, or None
    (no audio, no ffprobe). A faststart must not change it: that is what a
    broken sound (silence, crackles) looks like after a remux."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                              "stream=sample_rate,channels", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out or None


def _lossless_audio_fix(path, sound):
    """A copy changed the sound's description: the header's rate is right
    but the AAC config inside is the encoder default. Rewrap the frames
    under a header built from that rate (repair_audio) instead of
    re-encoding."""
    rate = sound.split(",")[0]
    if not rate.isdigit() or int(rate) not in _RATES:
        return False
    backup = path + ".orig.part"
    try:
        shutil.copy2(path, backup)
        if repair_audio(path, int(rate)) and _audio_signature(path) == sound and is_faststart(path):
            return True
        os.replace(backup, path)  # not better: back to the original, re-encode instead
    except OSError:
        pass
    finally:
        try:
            os.remove(backup)
        except OSError:
            pass
    return False


def _remux(path, suffix, codec_args, label, duration, sound):
    """Stream copy of `path` with `codec_args`, index at the start. The file
    is replaced only if the copy is faststart and its sound unchanged.
    Returns True if the file was rewritten."""
    tmp = path + suffix
    ok = run_ffmpeg(["-i", path, *codec_args, "-movflags", "+faststart", "-f", "mp4", tmp],
                    label, duration)
    if (ok and os.path.exists(tmp) and os.path.getsize(tmp) > 0 and is_faststart(tmp)
            and _audio_signature(tmp) == sound):
        os.replace(tmp, path)
        return True
    try:
        os.remove(tmp)
    except OSError:
        pass
    return False


def ensure_faststart(path, quiet=False):
    """Move the index to the start of an .mp4 if it isn't there yet.
    Returns True if the file was rewritten."""
    if not path or not path.lower().endswith((".mp4", ".m4v")) or not os.path.isfile(path):
        return False
    if not shutil.which("ffmpeg"):
        return False
    rate = real_sample_rate(path)
    if rate:
        # Sound described wrongly (old anime conversions): fixed losslessly,
        # and the repaired file is written with the index at the start.
        print_status(f"Son mal décrit dans {os.path.basename(path)} (fichier déclaré en mauvaise "
                     f"fréquence, vraie : {rate} Hz) : réparation sans ré-encodage...", "loading")
        if repair_audio(path, rate):
            print_status(f"Son réparé : {os.path.basename(path)}", "success")
            return True
    if is_faststart(path) is not False:
        return False
    if not quiet:
        print_status(f"Faststart (index en début de fichier, sans ré-encodage) : {os.path.basename(path)}", "loading")
    duration = media_duration(path)
    sound = _audio_signature(path)
    attempts = (
        (["-map", "0", "-c", "copy", "-ignore_unknown"], "⚡ Faststart"),  # every stream
        (["-c", "copy"], "⚡ Faststart"),                                 # the main ones
        # Sound described wrongly by the file (old PyAV conversions): a copy
        # would carry the wrong description, so the sound is re-encoded from
        # what the decoder actually reads. Picture still copied.
        (["-map", "0:v:0?", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"],
         "⚡ Faststart (réparation du son)"),
    )
    for codec_args, label in attempts:
        if "aac" in codec_args and sound and _lossless_audio_fix(path, sound):
            return True
        if _remux(path, ".faststart.part", codec_args, label, duration, sound):
            return True
    print_status(f"Impossible d'optimiser {os.path.basename(path)} (fichier laissé tel quel)", "warning")
    return False


def _video_codec_tag(path):
    """(codec, codec_tag) de la 1re piste vidéo, ex. ('hevc', 'hev1'), ou
    (None, None)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=codec_name,codec_tag_string", "-of", "default=nokey=1:noprint_wrappers=1",
                              path], capture_output=True, text=True, timeout=60).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    return (out[0] if out else None), (out[1] if len(out) > 1 else None)


def ensure_hvc1(path):
    """HEVC taggé `hev1` → `hvc1`, sans ré-encodage : beaucoup de clients Plex
    (navigateur, Apple, TV) gèrent mal `hev1` (image ou SON absents). Le son et
    l'image sont recopiés tels quels. True si le fichier a été réécrit."""
    if not path or not path.lower().endswith((".mp4", ".m4v", ".mov")) or not os.path.isfile(path):
        return False
    if not shutil.which("ffmpeg"):
        return False
    codec, tag = _video_codec_tag(path)
    if codec != "hevc" or (tag or "").lower() == "hvc1":
        return False  # pas du HEVC, ou déjà hvc1
    print_status(f"HEVC {tag or '?'}→hvc1 (compatibilité Plex, sans ré-encodage) : "
                 f"{os.path.basename(path)}", "loading")
    return _remux(path, ".hvc1.part", ["-map", "0", "-c", "copy", "-tag:v", "hvc1", "-ignore_unknown"],
                  "🔧 hev1→hvc1", media_duration(path), _audio_signature(path))


def fix_folder(folder):
    """Rewrite every .mp4 under folder that isn't faststart, whose sound is
    described wrongly, or whose HEVC is tagged hev1 (Plex compat)."""
    if not shutil.which("ffmpeg"):
        print_status("ffmpeg est nécessaire pour optimiser les fichiers.", "error")
        return 0
    checked = fixed = 0
    for root, _, files in os.walk(folder):
        for name in sorted(files):
            if name.lower().endswith((".mp4", ".m4v")):
                checked += 1
                path = os.path.join(root, name)
                # hvc1 first: its rewrite is already faststart, so
                # ensure_faststart then has nothing left to move.
                changed = ensure_hvc1(path)
                if ensure_faststart(path) or changed:
                    fixed += 1
    print_status(f"{folder} : {checked} fichier(s) .mp4 vérifié(s), {fixed} optimisé(s) ou réparé(s).", "success")
    return fixed
