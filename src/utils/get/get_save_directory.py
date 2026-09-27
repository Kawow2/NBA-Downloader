import os
import re
from src.var import Colors, print_status, print_separator
from src.utils.config.config import get_setting

def sanitize_path(path):
    return re.sub(r'[<>:"|?*]', '', path)


_SEASON_FOLDER_RE = re.compile(r'^(season|saison)\s*0*(\d+)$', re.IGNORECASE)

# Plex's layout for a TV library:
#   <Anime folder>/Dragon Ball/Season 01/Dragon Ball - S01E01.mp4
#   <Anime folder>/Dragon Ball/Specials/Dragon Ball - S00E01.mp4  (films, OAV)
SPECIALS_FOLDER = "Specials"
_SPECIALS_RE = re.compile(r'^(film|movie|oav|ova|special)', re.IGNORECASE)
_LOWERCASE_WORDS = {"a", "an", "and", "de", "e", "ga", "in", "ka", "mo", "na", "ni", "no", "of",
                    "on", "the", "to", "wa", "wo"}
_FOLDER_TAG_RE = re.compile(r'\s*[\[{](tvdb|imdb|imdbid|tmdb)-[\w]+[\]}]\s*$', re.IGNORECASE)


def plex_show_name(anime_name):
    """Show title for folder and file names: the site's slug
    "dragon-ball-z" becomes "Dragon Ball Z" (what Plex searches for); a
    name that already is a title is kept."""
    if not anime_name:
        return "Unknown Anime"
    name = re.sub(r'[<>:"/\\|?*]', '', anime_name).strip()
    if " " in name or name != name.lower() or "-" not in name:
        return name[:1].upper() + name[1:] if name else "Unknown Anime"
    words = [w for w in name.split("-") if w]
    return " ".join(w if i and w in _LOWERCASE_WORDS else w[:1].upper() + w[1:]
                    for i, w in enumerate(words))


def plex_season_folder(saison_info):
    """"saison2" -> "Season 02"; films and OAV -> "Specials"."""
    info = (saison_info or "").strip()
    if _SPECIALS_RE.match(info):
        return SPECIALS_FOLDER
    m = re.search(r'\d+', info)
    return f"Season {int(m.group()) if m else 1:02d}"


def plex_season_number(season_dir, season_number):
    """Season in the SxxExx file name: 0 inside Specials."""
    if os.path.basename(os.path.normpath(season_dir or "")) == SPECIALS_FOLDER:
        return 0
    return season_number if season_number else 1


def _rename(src, dst):
    try:
        os.rename(src, dst)
        print_status(f"Dossier renommé pour Plex : {src} → {dst}", "info")
        return True
    except OSError as e:
        print_status(f"Impossible de renommer {src} ({e.strerror or e}), il est gardé tel quel", "warning")
        return False


def _show_dir(base_path, anime_name, show):
    """The show's folder under base_path. A folder from an older version
    (named after the slug, e.g. "dragon-ball", possibly with a
    {tvdb-...} tag) is renamed to the Plex title, with its episode files."""
    try:
        entries = [d for d in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, d))]
    except OSError:
        return os.path.join(base_path, show)
    slug = re.sub(r'[<>:"/\\|?*]', '', anime_name or "").strip()
    for wanted in (show, slug):
        for d in entries:
            if _FOLDER_TAG_RE.sub("", d) != wanted:
                continue
            if wanted == show or not slug or slug == show:
                return os.path.join(base_path, d)
            target = os.path.join(base_path, show + d[len(slug):])
            if os.path.exists(target) or not _rename(os.path.join(base_path, d), target):
                return os.path.join(base_path, d)
            _rename_episode_files(target, slug, show)
            return target
    return os.path.join(base_path, show)


def _rename_episode_files(folder, old_name, new_name):
    """"dragon-ball - S01E01.mp4" -> "Dragon Ball - S01E01.mp4"."""
    prefix = f"{old_name} - S"
    for root, _, files in os.walk(folder):
        for f in files:
            if f.startswith(prefix):
                target = os.path.join(root, new_name + f[len(old_name):])
                if not os.path.exists(target):
                    try:
                        os.rename(os.path.join(root, f), target)
                    except OSError:
                        pass


def _season_dir(show_dir, saison_info, season):
    """Season folder inside show_dir: an existing "Season 1" (Sonarr) is
    kept; an older "saison1" is renamed to "Season 01"."""
    if season == SPECIALS_FOLDER:
        return os.path.join(show_dir, season)
    existing = find_existing_season_dir(show_dir, saison_info)
    if not existing or existing.lower().startswith("season"):
        return os.path.join(show_dir, existing or season)
    target = os.path.join(show_dir, season)
    if not os.path.exists(target) and _rename(os.path.join(show_dir, existing), target):
        return target
    return os.path.join(show_dir, existing)


def find_existing_season_dir(anime_dir, saison_info):
    """If --dest points straight at a folder Sonarr/Plex already manages, that
    folder can already contain a season subfolder for this season under a
    different naming convention (Sonarr writes "Season 1", the site's own
    slug is "saison1"/"Saison 1"). Creating a second, differently-named one
    scatters episodes across two folders that neither Sonarr nor this script
    ever reconciles - reuse whichever one already exists instead. Returns the
    existing folder's name, or None if there isn't one / the number can't be
    read from saison_info."""
    m = re.search(r'\d+', saison_info or "")
    if not m or not os.path.isdir(anime_dir):
        return None
    season_num = m.group()
    try:
        candidates = []
        for d in os.listdir(anime_dir):
            if not os.path.isdir(os.path.join(anime_dir, d)):
                continue
            fm = _SEASON_FOLDER_RE.match(d)
            if fm and fm.group(2) == season_num:
                candidates.append(d)
    except OSError:
        return None
    if not candidates:
        return None
    # Prefer Sonarr's own spelling when both exist, since that's the one Sonarr
    # (and therefore Plex, if the library is Sonarr-managed) actually scans.
    for d in candidates:
        if d.lower().replace(" ", "") == f"season{season_num}":
            return d
    return candidates[0]


def format_save_path(anime_name, saison_info, base_path=None):
    template = get_setting("save_template", "./videos/{anime}/{season}")
    # Anime folder chosen in the Anime/NBA menu (src/launcher.py):
    # "<folder>/<anime>/<season>", like --dest.
    base_path = base_path or os.environ.get("ANIME_DEFAULT_DIR")

    fmt_args = {
        "anime": plex_show_name(anime_name),
        "season": plex_season_folder(saison_info),
    }

    if base_path:
        show_dir = _show_dir(base_path, anime_name, fmt_args["anime"])
        return _season_dir(show_dir, saison_info, fmt_args["season"])

    try:
        formatted_path = template.format(**fmt_args)
        return os.path.normpath(formatted_path)
    except Exception:
        return os.path.join("./videos", fmt_args["anime"], fmt_args["season"])

def get_save_directory(anime_name=None, saison_info=None):
    formatted_path = format_save_path(anime_name, saison_info)

    print(f"\n{Colors.BOLD}{Colors.HEADER}📁 SAVE LOCATION{Colors.ENDC}")
    print_separator()

    print(f"{Colors.OKCYAN}Current save path (from config): {Colors.ENDC}{formatted_path}")

    change = input(f"{Colors.BOLD}Press Enter to confirm or type new absolute path: {Colors.ENDC}").strip()

    if change:
        save_dir = change
    else:
        save_dir = formatted_path

    # The folder itself is only created when the first file is written, so
    # cancelling before the download leaves nothing behind - here we just
    # check that it could be created (nearest existing parent is writable).
    ancestor = os.path.abspath(save_dir)
    while not os.path.exists(ancestor):
        parent = os.path.dirname(ancestor)
        if parent == ancestor:
            break
        ancestor = parent

    if os.path.isdir(ancestor) and os.access(ancestor, os.W_OK):
        print_status(f"Save directory confirmed: {os.path.abspath(save_dir)}", "success")
        return save_dir

    print_status(f"Cannot write to {save_dir}", "error")
    default_fallback = "./videos/"
    print_status(f"Using fallback: {default_fallback}", "info")
    return default_fallback
