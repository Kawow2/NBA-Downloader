"""Sous-titres en fichiers « sidecar » à côté du .mp4, pour le VOSTFR / VOST.

Plex (comme Jellyfin / Kodi) lit automatiquement un fichier de sous-titres qui
porte le même nom que la vidéo + un code langue :

    Inception (2010)/Inception (2010).fr.srt
    Inception (2010)/Inception (2010).en.srt
    …/Game of Thrones (2011) - S01E01.fr.srt

Chacun apparaît comme une piste **activable/désactivable** dans le lecteur (FR,
anglais…), ce que veut l'utilisateur : pouvoir choisir sur Plex.

Par défaut on récupère **le français ET la VO** (langue originale du média,
l'anglais le plus souvent). On lit les pistes de deux façons, dans l'ordre :
  1. les URLs portées par la Source (un lecteur qui expose ses pistes) ;
  2. à défaut, via yt-dlp (extracteur générique) sur l'URL du lecteur.

Les .vtt sont convertis en .srt (format le plus universel pour Plex).

Recalage fréquence d'images : certains sous-titres viennent d'une source 25 fps
(PAL) alors que la vidéo est en 23,976 fps → ils collent au début puis dérivent.
Le réglage `films_subtitles_fps` (ou --sub-fps 25) réétire les temps d'un facteur
sub_fps / fps_vidéo (le « décalage » de Plex ne corrige qu'un offset fixe)."""
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests

from src.var import DEFAULT_USER_AGENT, print_status
from src.utils.config.config import get_setting

# Pour chaque code ISO, les mots qui désignent la langue dans un code de piste
# ou un libellé ("English", "Français", "VOSTFR"…).
_LANG_NAMES = {
    "fr": ["fr", "fre", "fra", "french", "francais", "français", "vostfr", "vf"],
    "en": ["en", "eng", "english", "anglais"],
    "es": ["es", "spa", "spanish", "espanol", "español", "espagnol"],
    "de": ["de", "ger", "deu", "german", "deutsch", "allemand"],
    "it": ["it", "ita", "italian", "italiano", "italien"],
    "pt": ["pt", "por", "portuguese", "portugues", "português", "portugais"],
    "ja": ["ja", "jpn", "japanese", "japonais"],
    "ko": ["ko", "kor", "korean", "coreen", "coréen"],
    "zh": ["zh", "chi", "zho", "chinese", "chinois"],
    "ru": ["ru", "rus", "russian", "russe"],
}


def _lang_re(code):
    names = _LANG_NAMES.get(code, [code])
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(rf"(^|[^a-z])({alt})([^a-z]|$)", re.I)


_MATCHERS = {code: _lang_re(code) for code in _LANG_NAMES}


def _matches(code, *texts):
    rx = _MATCHERS.get(code) or _lang_re(code)
    return any(rx.search(str(t or "")) for t in texts)


# --------------------------------------------------------------- réglages
def subtitles_setting():
    """Valeur brute du réglage `films_subtitles` (défaut 'fr,vo')."""
    return (get_setting("films_subtitles") or "fr,vo").strip().lower()


def subtitles_enabled():
    return subtitles_setting() not in ("off", "non", "no", "0", "none", "aucun", "")


# Alias acceptés dans le réglage, en plus des codes ISO.
_SETTING_ALIASES = {"all": "fr,vo", "tout": "fr,vo", "tous": "fr,vo", "vo,fr": "fr,vo",
                    "fra": "fr", "french": "fr"}


def wanted_langs(media=None):
    """Liste de codes ISO à récupérer, selon le réglage et la VO du média.
    'fr,vo' -> ['fr', <langue originale>] ; 'fr' -> ['fr'] ; 'all' -> fr + vo ;
    sinon une liste de codes séparés par virgule ('vo' = langue originale)."""
    if not subtitles_enabled():
        return []
    setting = subtitles_setting()
    setting = _SETTING_ALIASES.get(setting, setting)
    raw = (getattr(media, "raw", None) or {}) if media is not None else {}
    vo = (raw.get("original_language") or "").lower() or "en"
    langs = [vo if tok in ("vo", "vost") else tok
             for tok in re.split(r"[\s,;+]+", setting) if tok]
    return list(dict.fromkeys(langs))  # dédoublonne en gardant l'ordre


# --------------------------------------------------------------- conversion
def _ts_to_srt(ts):
    """'00:01:02.345' ou '01:02.345' -> '00:01:02,345' (format SRT)."""
    ts = ts.strip().replace(".", ",")
    head, _, ms = ts.partition(",")
    parts = head.split(":")
    if len(parts) == 2:
        parts = ["00"] + parts
    parts = [p.zfill(2) for p in parts]
    return f"{':'.join(parts)},{(ms or '000')[:3].ljust(3, '0')}"


_CUE_TIME = re.compile(r"(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}\s*-->\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}")
# Lignes structurelles VTT (en-tête, blocs) : surtout utile quand on concatène
# plusieurs segments VTT (chaque segment rouvre par « WEBVTT »).
_VTT_STRUCT = re.compile(r"^(WEBVTT|NOTE|STYLE|REGION|X-TIMESTAMP-MAP)", re.I)


def vtt_to_srt(text):
    """Convertit un WebVTT (éventuellement plusieurs segments concaténés) en SRT."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues, cur = [], None
    for line in lines:
        if "-->" in line and _CUE_TIME.search(line):
            if cur:
                cues.append(cur)
            a, _, b = line.partition("-->")
            cur = {"start": a.strip(), "end": b.strip().split(" ")[0], "text": []}
        elif cur is not None:
            # Fin de bloc : ligne vide OU ligne structurelle (en-tête d'un
            # segment suivant) — sans quoi « WEBVTT » se collerait au texte.
            if line.strip() == "" or _VTT_STRUCT.match(line.strip()):
                cues.append(cur)
                cur = None
            else:
                cur["text"].append(re.sub(r"<[^>]+>", "", line))
    if cur:
        cues.append(cur)
    out = []
    for i, c in enumerate(cues, 1):
        body = "\n".join(c["text"]).strip()
        if not body:
            continue
        out.append(f"{i}\n{_ts_to_srt(c['start'])} --> {_ts_to_srt(c['end'])}\n{body}\n")
    return "\n".join(out)


def _write_srt(srt, stem, code):
    """Écrit le SRT `srt` en <stem>.<code>.srt. Renvoie le chemin, ou None."""
    path = f"{stem}.{code}.srt"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(srt)
    except OSError:
        return None
    return path


# Une seule session HTTP (connexions réutilisées : une playlist HLS de
# sous-titres compte des centaines de segments).
_http = requests.Session()
_http.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=16))


def _fetch(url, referer=None, timeout=30):
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    if referer:
        headers["Referer"] = referer
    try:
        r = _http.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.text
    except (requests.RequestException, OSError):
        return None


# ------------------------------------------------------------------ yt-dlp
def _ytdlp_subtitles(embed_url, referer):
    """Dict {code_langue: [pistes]} renvoyé par yt-dlp pour ce lecteur, ou {}.
    Silencieux si le lecteur n'est pas géré."""
    if not embed_url:
        return {}
    try:
        import yt_dlp
    except ImportError:
        return {}

    class _Silent:
        def debug(self, m): pass
        info = warning = error = debug

    opts = {
        "skip_download": True, "quiet": True, "no_warnings": True, "logger": _Silent(),
        "socket_timeout": 30,
        "http_headers": {"User-Agent": DEFAULT_USER_AGENT,
                         **({"Referer": referer} if referer else {})},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(embed_url, download=False)
    except Exception:
        return {}
    return (info.get("subtitles") or {}) if isinstance(info, dict) else {}


def _pick_track(subs, code):
    """Première piste de `subs` correspondant au code langue (par clé puis
    libellé), en préférant un .vtt/.srt. Renvoie l'URL, ou None."""
    matched = []
    for lang, tracks in (subs or {}).items():
        tracks = tracks or []
        if _matches(code, lang):
            matched.extend(tracks)
        else:
            for t in tracks:
                if _matches(code, t.get("name")):
                    matched.append(t)
    matched.sort(key=lambda t: 0 if (t.get("ext") in ("vtt", "srt")) else 1)
    for t in matched:
        if t.get("url"):
            return t["url"]
    return None


# ---------------------------------------------------- recalage fréquence d'images
_SRT_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")


def _shift_ts(m, factor):
    total = (((int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))) * 1000
              + int(m.group(4))) * factor)
    total = max(0, int(round(total)))
    h, total = divmod(total, 3600000)
    mi, total = divmod(total, 60000)
    s, ms = divmod(total, 1000)
    return f"{h:02d}:{mi:02d}:{s:02d},{ms:03d}"


def rescale_srt_text(text, factor):
    """Multiplie tous les timecodes SRT par `factor` (ex. 25/23.976)."""
    return _SRT_TS.sub(lambda m: _shift_ts(m, factor), text)


def _video_fps(path):
    """Fréquence d'images réelle de la vidéo (ex. 23.976), ou None."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=r_frame_rate", "-of", "default=nokey=1:noprint_wrappers=1", path],
                             capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    num, _, den = out.partition("/")
    try:
        d = float(den) if den else 1.0
        return float(num) / d if d else None
    except ValueError:
        return None


def _maybe_rescale(srt_path, video_path):
    """Si films_subtitles_fps est réglé (ex. 25), réétire le .srt pour qu'il
    colle à la vraie fréquence de la vidéo."""
    setting = get_setting("films_subtitles_fps")
    if not setting:
        return
    try:
        sub_fps = float(str(setting).replace(",", "."))
    except (TypeError, ValueError):
        return
    if sub_fps <= 0:
        return
    vfps = _video_fps(video_path)
    if not vfps:
        return
    factor = sub_fps / vfps
    if abs(factor - 1) < 0.002:  # écart négligeable (< 0,2 %)
        return
    try:
        with open(srt_path, encoding="utf-8") as f:
            txt = f.read()
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(rescale_srt_text(txt, factor))
        print_status(f"Sous-titres recalés {sub_fps:g}→{vfps:.3f} fps (×{factor:.4f}) : "
                     f"{os.path.basename(srt_path)}", "info")
    except OSError:
        pass


# --------------------------------------------- validation / formats des pistes
def _hls_subs_to_srt(playlist, playlist_url, referer):
    """Playlist HLS de sous-titres (#EXTM3U) → SRT : récupère les segments .vtt
    référencés (en parallèle, ordre conservé) et les concatène."""
    segs = [ln.strip() for ln in playlist.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    seg_urls = [seg if seg.startswith("http") else urljoin(playlist_url, seg) for seg in segs[:4000]]
    with ThreadPoolExecutor(max_workers=16) as pool:
        texts = pool.map(lambda u: _fetch(u, referer=referer), seg_urls)
        parts = [t for t in texts if t and "-->" in t]
    if not parts:
        return None
    srt = vtt_to_srt("\n\n".join(parts))
    return srt if srt.strip() else None


def _subtitle_to_srt(text, url, referer):
    """Transforme un contenu en SRT valide, ou None si ce n'est pas un sous-titre
    (JSON/HTML/erreur). Gère VTT, SRT et les playlists HLS de sous-titres."""
    if not text:
        return None
    head = text.lstrip()[:16].upper()
    if head.startswith("#EXTM3U"):
        return _hls_subs_to_srt(text, url, referer)
    if head.startswith("WEBVTT") or "-->" in text:
        srt = vtt_to_srt(text)
        return srt if srt.strip() else None
    return None  # ni VTT ni SRT ni playlist → on refuse (évite l'erreur Plex)


def _valid_srt_file(path):
    """True si le .srt existant contient au moins un timing (sinon il est cassé
    et Plex le refuse : on le régénère)."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return "-->" in f.read(4000)
    except OSError:
        return False


def _first_valid(urls, stem, code, referer):
    """Télécharge les `urls` dans l'ordre jusqu'à obtenir un sous-titre valide,
    écrit en <stem>.<code>.srt. Renvoie le chemin, ou None."""
    for url in urls:
        raw = _fetch(url, referer=referer)
        srt = _subtitle_to_srt(raw, url, referer) if raw else None
        if srt:
            path = _write_srt(srt, stem, code)
            if path:
                return path
    return None


# -------------------------------------------------------------------- public
def download_for(source, video_out_path, media=None, season=None, episode=None):
    """Télécharge les sous-titres voulus (FR + VO par défaut) en sidecars à côté
    de `video_out_path`. Renvoie la liste des .srt écrits. Best-effort, jamais
    bloquant."""
    langs = wanted_langs(media)
    if not langs:
        return []
    stem = os.path.splitext(video_out_path)[0]
    referer = getattr(source, "page_referer", None)
    explicit = getattr(source, "subtitles", None) or []

    ytdlp_subs = None  # chargé paresseusement (une seule fois)
    written = []
    for code in langs:
        # Déjà présent et valide ? Sinon on supprime le fichier cassé et on refait.
        existing = next((stem + e for e in (f".{code}.srt", f".{code}.vtt")
                         if os.path.exists(stem + e)), None)
        if existing:
            if _valid_srt_file(existing):
                written.append(existing)
                continue
            try:
                os.remove(existing)
            except OSError:
                pass

        # Candidats (dans l'ordre) : pistes portées par la Source, puis — si
        # aucune ne donne un SRT valide — yt-dlp (interrogé une seule fois).
        candidates = [sub["url"] for sub in explicit
                      if isinstance(sub, dict) and sub.get("url")
                      and _matches(code, sub.get("lang"), sub.get("label"))]
        path = _first_valid(candidates, stem, code, referer)
        if not path:
            if ytdlp_subs is None:
                probe_url = getattr(source, "resolved_stream", None) or getattr(source, "url", None)
                probe_ref = getattr(source, "resolved_referer", None) or referer
                ytdlp_subs = _ytdlp_subtitles(probe_url, probe_ref)
            yurl = _pick_track(ytdlp_subs, code)
            if yurl and yurl not in candidates:
                path = _first_valid([yurl], stem, code, referer)
        if path:
            _maybe_rescale(path, video_out_path)
            written.append(path)
            print_status(f"Sous-titres {code.upper()} : {os.path.basename(path)}", "success")

    if not written:
        print_status("Sous-titres introuvables pour ce lecteur (vidéo gardée).", "info")
    return written
