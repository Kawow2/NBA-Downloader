"""Download one game part to .mp4.

Two backends:
  * the project's own extractors (VOE, Filemoon, LuluStream, Vidmoly,
    Uqload, Sibnet, ...) which return an m3u8/mp4 URL, then ffmpeg copies it
    straight into an .mp4 (no re-encoding);
  * yt-dlp for everything else (OK.ru, Dailymotion, Streamtape, Mixdrop,
    Dood, VK, generic <video>/jwplayer pages...).
Hosts the extractors know go through them first, every other host through
yt-dlp first; whichever fails falls back to the other. OK.ru and
Dailymotion fall back to the built-in extractors in hosts.py instead.
"""
import os
import re
import shutil
import socket
import subprocess
import sys
from urllib.parse import urlparse

from src.var import Colors, DEFAULT_USER_AGENT, print_status
from src.utils.check.check_ffmpeg_installed import check_ffmpeg_installed
from src.utils.download.verify_video_file import verify_video_file
from src.nba.hosts import builtin_stream, has_builtin, patch_ytdlp

# Download settings, set by main.py (--quality / --threads, remembered).
# OK.ru throttles each connection, so throughput comes from fetching many
# HLS fragments at once; and a 1080p cap keeps a game at a few GB instead
# of ~20 GB in the host's top (1440p/4K) quality.
SETTINGS = {"max_height": 1080, "threads": 32}


def configure(max_height=None, threads=None):
    SETTINGS["max_height"] = max_height
    if threads:
        SETTINGS["threads"] = max(1, int(threads))


EXTRACTOR_HOSTS = (
    "vidzy", "luluvdo", "lulustream", "filemoon", "bysesukior", "voe", "vidmoly", "sendvid",
    "embed4me", "video.sibnet.ru", "uqload", "oneupload", "ansembed", "dingtezuni", "mivalyo",
    "smoothpre", "movearnpre",
)


def _origin(url):
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _cleanup(*paths):
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def _finish(tmp_path, out_path):
    ok, reason = verify_video_file(tmp_path)
    if not ok:
        print_status(f"Fichier invalide ({reason})", "error")
        _cleanup(tmp_path)
        return False
    os.replace(tmp_path, out_path)
    return True


# ------------------------------------------------------------------ ffmpeg
def ffmpeg_copy(stream_url, out_path, referer=None, user_agent=DEFAULT_USER_AGENT):
    """Copy an HLS/MP4 stream into an .mp4 without re-encoding."""
    tmp = out_path + ".part"
    headers = ""
    if referer:
        headers = f"Referer: {referer}\r\nOrigin: {_origin(referer)}\r\n"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
           "-user_agent", user_agent]
    if headers:
        cmd += ["-headers", headers]
    if stream_url.startswith("http") and ".m3u8" not in stream_url:
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
    cmd += ["-i", stream_url, "-c", "copy", "-movflags", "+faststart", "-f", "mp4", tmp]
    print_status("Téléchargement avec ffmpeg (copie directe en .mp4)...", "loading")
    try:
        result = subprocess.run(cmd)
    except FileNotFoundError:
        return False
    if result.returncode != 0 or not os.path.exists(tmp):
        print_status("ffmpeg a échoué sur ce flux.", "error")
        _explain_dns_failure(stream_url)
        _cleanup(tmp)
        return False
    return _finish(tmp, out_path)


def _explain_dns_failure(url):
    """Video CDNs use throwaway domain names that some DNS servers (ISP
    DNS, NextDNS/AdGuard/Pi-hole filters, antivirus web shields) don't
    resolve or block: say so instead of a bare ffmpeg I/O error."""
    host = urlparse(url).hostname
    if not host:
        return
    try:
        socket.getaddrinfo(host, 443)
    except socket.gaierror:
        print_status(f"Votre DNS ne résout pas {host} (serveur vidéo) : le blocage vient de votre réseau, "
                     "pas du site. Essayez un autre DNS (ex. 1.1.1.1 ou 8.8.8.8) ou un autre serveur.", "warning")


def ffmpeg_remux(src, out_path):
    tmp = out_path + ".part"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
           "-c", "copy", "-movflags", "+faststart", "-f", "mp4", tmp]
    if subprocess.run(cmd).returncode != 0:
        _cleanup(tmp)
        return False
    if _finish(tmp, out_path):
        _cleanup(src)
        return True
    return False


def _stream_signature(path):
    """Codec parameters that must match for a lossless concat, or None if
    ffprobe isn't available."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,width,height,sample_rate,channels,time_base",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


def _probe(path):
    """(width, height, fps, has_audio, duration) of a video, via ffprobe."""
    def run(args):
        return subprocess.run(["ffprobe", "-v", "error"] + args + [path],
                              capture_output=True, text=True, timeout=60).stdout.strip()
    try:
        video = run(["-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate", "-of", "csv=p=0"])
        audio = run(["-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0"])
        duration = run(["-show_entries", "format=duration", "-of", "csv=p=0"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    try:
        w, h, rate = video.split(",")[:3]
        num, _, den = rate.partition("/")
        fps = float(num) / float(den or 1)
        return int(w), int(h), round(fps, 3) or 25, bool(audio), float(duration or 0)
    except (ValueError, ZeroDivisionError):
        return None


def _merge_reencode(paths, out_path):
    """Join parts whose encodings differ (a part came from a fallback
    server): re-encode everything to the first part's resolution and frame
    rate. Much slower than a copy, but always yields one file."""
    probes = [_probe(p) for p in paths]
    if None in probes:
        return False
    width, height, fps = probes[0][0], probes[0][1], probes[0][2]
    inputs, chains, labels = [], [], ""
    for i, (p, pr) in enumerate(zip(paths, probes)):
        inputs += ["-i", p]
    extra = len(paths)
    for i, pr in enumerate(probes):
        chains.append(f"[{i}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                      f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}]")
        if pr[3]:
            chains.append(f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:
            # A part without sound: silence of the same length.
            inputs += ["-f", "lavfi", "-t", str(pr[4] or 1), "-i", "anullsrc=r=48000:cl=stereo"]
            chains.append(f"[{extra}:a:0]anull[a{i}]")
            extra += 1
        labels += f"[v{i}][a{i}]"
    graph = ";".join(chains) + f";{labels}concat=n={len(paths)}:v=1:a=1[v][a]"
    tmp = out_path + ".part"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats"] + inputs + [
        "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", "-f", "mp4", tmp]
    print_status("Les parties n'ont pas le même encodage (serveurs différents) : fusion avec ré-encodage "
                 "(plus lent, plusieurs minutes)...", "loading")
    ok = subprocess.run(cmd).returncode == 0 and _finish(tmp, out_path)
    if not ok:
        _cleanup(tmp)
    return ok


def merge_parts(paths, out_path):
    """Join the parts into one .mp4: a lossless copy when they share the
    same encoding (one server), otherwise (or if the copy fails) a
    re-encode, so the result is always a single file."""
    signatures = [_stream_signature(p) for p in paths]
    if None not in signatures and any(sig != signatures[0] for sig in signatures):
        return _merge_reencode(paths, out_path)
    list_path = out_path + ".txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for p in paths:
            escaped = os.path.abspath(p).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    tmp = out_path + ".part"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
           "-i", list_path, "-c", "copy", "-movflags", "+faststart", "-f", "mp4", tmp]
    print_status("Fusion des parties en un seul fichier...", "loading")
    try:
        ok = subprocess.run(cmd).returncode == 0 and _finish(tmp, out_path)
    finally:
        _cleanup(list_path)
    if not ok:
        _cleanup(tmp)
        return _merge_reencode(paths, out_path)
    return ok


# ------------------------------------------------------ project extractors
def _download_with_extractors(embed_url, out_path):
    from src.utils.fetch.fetch_video_source import fetch_video_source

    stream = fetch_video_source(embed_url)
    if not stream:
        return False
    referer = _origin(embed_url) + "/"
    if stream.startswith("LULU_DEFERRED:"):
        from src.utils.extract.extract_luluvdo_video_source import extract_luluvdo_video_source
        stream = extract_luluvdo_video_source(stream[len("LULU_DEFERRED:"):])
        if not stream:
            return False

    if check_ffmpeg_installed():
        return ffmpeg_copy(stream, out_path, referer=referer)

    # No ffmpeg: the project's own segment downloader + PyAV conversion.
    from src.utils.download.download_video import download_video
    from src.utils.ts.convert_ts_to_mp4 import convert_ts_to_mp4
    ok, path = download_video(stream, out_path, use_ts_threading=True, url=embed_url,
                              automatic_mp4=True, interactive=False)
    if not ok or not path:
        return False
    if path.endswith(".ts"):
        ok, mp4 = convert_ts_to_mp4(path, out_path, "av")
        if not ok:
            return False
        _cleanup(path)
        if mp4 != out_path:
            os.replace(mp4, out_path)
    return os.path.exists(out_path)


# ------------------------------------------------------------------ yt-dlp
def _download_with_ytdlp(embed_url, out_path, page_url, origin=None):
    try:
        import yt_dlp
    except ImportError:
        print_status("yt-dlp n'est pas installé (pip install -U yt-dlp).", "warning")
        return False

    patch_ytdlp()
    has_ffmpeg = check_ffmpeg_installed()
    base = os.path.splitext(out_path)[0] + ".ytdlp"
    opts = {
        "outtmpl": base + ".%(ext)s",
        "format": "bv*+ba/b" if has_ffmpeg else "b[ext=mp4]/b",
        # Best quality up to the cap (largest resolution <= max_height).
        "format_sort": [f"res:{SETTINGS['max_height']}"] if SETTINGS["max_height"] else [],
        "merge_output_format": "mp4",
        "http_headers": {"Referer": page_url or embed_url, "User-Agent": DEFAULT_USER_AGENT,
                         **({"Origin": origin} if origin else {})},
        "noplaylist": True,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": SETTINGS["threads"],
        "overwrites": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": False,
    }
    print_status("Téléchargement avec yt-dlp...", "loading")
    info, error = None, None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(embed_url, download=True)
    except Exception as e:
        error = str(e).splitlines()[0][:200]

    path = None
    for d in (info or {}).get("requested_downloads") or []:
        path = d.get("filepath") or d.get("_filename")
    if not path or not os.path.exists(path):
        # A post-processing failure (e.g. ffprobe missing) still leaves the
        # downloaded file behind: remuxing it ourselves below is enough.
        folder = os.path.dirname(base) or "."
        prefix = os.path.basename(base) + "."
        found = [os.path.join(folder, f) for f in os.listdir(folder)
                 if f.startswith(prefix) and not f.endswith((".part", ".ytdl")) and ".part-Frag" not in f]
        path = max(found, key=os.path.getsize) if found else None
    if not path or not os.path.exists(path):
        if error:
            print_status(f"yt-dlp : {error}", "error")
        return False
    if error:
        if not has_ffmpeg:
            print_status(f"yt-dlp : {error}", "error")
            return False
        return ffmpeg_remux(path, out_path)

    if path.lower().endswith(".mp4"):
        return _finish(path, out_path)
    if has_ffmpeg:
        return ffmpeg_remux(path, out_path)
    # Keep what we got rather than throwing a finished download away.
    kept = os.path.splitext(out_path)[0] + os.path.splitext(path)[1]
    os.replace(path, kept)
    print_status(f"ffmpeg absent : fichier gardé tel quel ({kept})", "warning")
    return True


def _download_builtin(embed_url, out_path, page_url):
    found = builtin_stream(embed_url, page_url, SETTINGS["max_height"])
    if not found:
        return False
    stream, referer = found
    # yt-dlp handles a raw HLS/MP4 URL itself: parallel fragments (fast)
    # and no ffmpeg needed. ffmpeg (sequential) is the last resort.
    print_status("Flux trouvé par l'extracteur intégré.", "success")
    if _download_with_ytdlp(stream, out_path, referer, origin=_origin(referer)):
        return True
    if not check_ffmpeg_installed():
        print_status(f"ffmpeg permettrait un autre essai : {ffmpeg_install_command()}", "warning")
        return False
    return ffmpeg_copy(stream, out_path, referer=referer)


def download_part(embed_url, out_path, page_url=None):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    host = (urlparse(embed_url).hostname or "").lower()
    path = urlparse(embed_url).path.lower()
    methods = [("extracteurs", lambda: _download_with_extractors(embed_url, out_path)),
               ("yt-dlp", lambda: _download_with_ytdlp(embed_url, out_path, page_url))]
    if not any(h in host for h in EXTRACTOR_HOSTS):
        methods.reverse()
    if has_builtin(embed_url):
        # OK.ru / Dailymotion: yt-dlp, then the built-in extractor (the
        # project's extractors don't know these hosts).
        methods = [methods[0], ("extracteur intégré", lambda: _download_builtin(embed_url, out_path, page_url))]
    if path.endswith((".m3u8", ".mp4")) and check_ffmpeg_installed():
        # A raw stream/file link: nothing to extract.
        methods.insert(0, ("ffmpeg", lambda: ffmpeg_copy(embed_url, out_path, referer=page_url)))
    for name, run in methods:
        try:
            if run():
                return True
        except Exception as e:
            print_status(f"Échec ({name}) : {e}", "error")
        print_status(f"{name} n'a pas pu télécharger {embed_url[:70]}", "warning")
    return False


# Leftovers of a download: our own "<file>.mp4.part" and concat lists,
# yt-dlp's ".part", ".part-FragN", ".ytdl" and its "<name>.ytdlp.*"
# intermediate files (renamed to the final name once complete).
_TEMP_FILE = re.compile(r"(\.part(-Frag\d+(\.part)?)?|\.ytdl|\.mp4\.txt)$|\.ytdlp\.", re.IGNORECASE)


def temp_files(folder, stem=None):
    """Temporary download files in folder (only those of stem if given)."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names
            if _TEMP_FILE.search(n) and (stem is None or n.startswith(stem))
            and os.path.isfile(os.path.join(folder, n))]


def ffmpeg_install_command():
    if os.name == "nt":
        return "winget install Gyan.FFmpeg  (puis rouvrez PowerShell)"
    if sys.platform == "darwin":
        return "brew install ffmpeg"
    for tool, cmd in (("apt-get", "sudo apt install ffmpeg"), ("dnf", "sudo dnf install ffmpeg"),
                      ("pacman", "sudo pacman -S ffmpeg"), ("zypper", "sudo zypper install ffmpeg"),
                      ("apk", "sudo apk add ffmpeg")):
        if shutil.which(tool):
            return cmd
    return "installez le paquet ffmpeg de votre distribution"


def ffmpeg_hint():
    if check_ffmpeg_installed():
        return
    print_status("ffmpeg n'est pas installé : il est fortement conseillé (fusion des parties, fichiers .mp4 propres).", "warning")
    print(f"   {Colors.DIM}Installez-le avec : {ffmpeg_install_command()}{Colors.ENDC}")
