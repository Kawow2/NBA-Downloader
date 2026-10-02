"""Anime / NBA menu, run by a small block at the top of main.py before the
anime downloader's own code (upstream: SertraFurr/anime-sama-nakanime-
downloader), so that file stays as close to upstream as possible.

    python main.py                  menu: Anime or NBA (+ settings)
    python main.py anime [options]  anime downloader, its own options
    python main.py nba [options]    NBA downloader, its own options
    python main.py --set-anime-dir PATH | --set-nba-dir PATH

Each category has its own folder (Plex library): config keys
anime_default_dir / nba_default_dir, asked the first time a category is
used, changeable in the settings; --anime-dir / --nba-dir override them for
one run (used by start.sh / start.ps1).
"""
import os
import signal
import sys

ANIME_DIR_ENV = "ANIME_DEFAULT_DIR"  # read by src/utils/get/get_save_directory.py

REQUIRED_MODULES = ("requests", "bs4", "tqdm", "yt_dlp", "curl_cffi", "Crypto", "av", "cloudscraper")

# Options that only exist in one of the programs: they tell which one a
# command line without "anime"/"nba"/"media" is meant for. Media is checked
# first in guess_category() because it shares --search with anime.
_ANIME_ONLY = ("--search", "--episodes", "--player", "--fast", "--mp4", "--tool", "--no-mal", "--latest")
_NBA_ONLY = ("--site", "--debug", "--set-default-dir")
_MEDIA_ONLY = ("--source", "--all", "--list-sources")
_LAUNCHER_OPTS = ("--anime-dir", "--nba-dir", "--media-dir",
                  "--set-anime-dir", "--set-nba-dir", "--set-media-dir", "--faststart")

# Per-category labels/examples used by the folder prompts.
_CAT_LABEL = {"anime": "des animes", "nba": "de la NBA", "media": "des films et séries"}
_CAT_TITLE = {"anime": "ANIME", "nba": "NBA", "media": "FILMS & SÉRIES"}
_CAT_EXAMPLE = {"anime": "/srv/plex/Anime", "nba": "/srv/plex/Sports/NBA", "media": "/srv/plex/Films"}

USAGE = """Utilisation :
  python main.py                      menu : Anime, NBA ou Films & Séries (+ réglages)
  python main.py anime [options]      téléchargeur d'animes (python main.py anime --help)
  python main.py nba [options]        téléchargeur NBA (python main.py nba --help)
  python main.py media [options]      téléchargeur films & séries (python main.py media --help)
  python main.py --set-anime-dir DOSSIER   dossier par défaut des animes
  python main.py --set-nba-dir DOSSIER     dossier par défaut de la NBA
  python main.py --set-media-dir DOSSIER   dossier par défaut des films & séries
  python main.py --faststart DOSSIER       optimise/répare les .mp4 déjà téléchargés (lecture immédiate dans Plex, son)
  --anime-dir / --nba-dir / --media-dir DOSSIER   dossier pour ce lancement seulement"""


def ensure_requirements():
    """Install requirements.txt into the running Python (the venv when
    started by start.sh / start.ps1) if a module is missing, e.g. one an
    update added."""
    import importlib.util
    missing = [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    if not missing:
        return
    import subprocess
    print(f"Modules manquants : {', '.join(missing)} — installation dans {sys.executable} ...")
    req = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "requirements.txt")
    subprocess.call([sys.executable, "-m", "pip", "install", "-r", req])
    importlib.invalidate_caches()
    still = [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    if still:
        print(f"Toujours manquants : {', '.join(still)}. Lancez : \"{sys.executable}\" -m pip install -r requirements.txt")
        if any(m in still for m in ("requests", "bs4", "tqdm")):
            sys.exit(1)


def fallback_dir(category):
    names = {"anime": "Anime", "nba": "NBA", "media": "Films & Séries"}
    return os.path.join(os.path.expanduser("~"), "Videos", names.get(category, category))


def _split_args(argv):
    """Separate the launcher's own options from the ones passed on to the
    chosen program."""
    own, rest = {}, []
    i = 0
    while i < len(argv):
        arg = argv[i]
        name, eq, value = arg.partition("=")
        if name in _LAUNCHER_OPTS:
            if eq:
                own[name] = value
            elif i + 1 < len(argv):
                own[name] = argv[i + 1]
                i += 1
            i += 1
            continue
        rest.append(arg)
        i += 1
    return own, rest


def guess_category(args):
    """"anime"/"nba"/"media" from options only one program has, or from
    --url. Media is tested before anime because they share --search."""
    names = {a.partition("=")[0] for a in args if a.startswith("--")}
    if names & set(_MEDIA_ONLY):
        return "media"
    if names & set(_ANIME_ONLY):
        return "anime"
    if names & set(_NBA_ONLY):
        return "nba"
    for i, arg in enumerate(args):
        url = arg.partition("=")[2] if arg.startswith("--url=") else (args[i + 1] if arg == "--url" and i + 1 < len(args) else "")
        url = url.lower()
        if "anime-sama" in url or "nakanime" in url:
            return "anime"
        if "basketball-video" in url:
            return "nba"
        if "archive.org" in url:
            return "media"
    return None


class Launcher:
    def __init__(self, overrides):
        from src.utils.config.config import get_setting, set_setting
        self.get_setting, self.set_setting = get_setting, set_setting
        self.overrides = overrides  # --anime-dir / --nba-dir for this run

    # ------------------------------------------------------------- folders
    def configured_dir(self, category):
        path = self.overrides.get(f"--{category}-dir") or self.get_setting(f"{category}_default_dir")
        # A relative path (e.g. "2", typed at the folder question as if it
        # were the menu) isn't a real choice: ask again.
        if path and not os.path.isabs(os.path.expanduser(path)):
            return None
        return path

    def folder(self, category):
        return self.configured_dir(category) or fallback_dir(category)

    def ask_folder(self, category, first_time=False):
        from src.var import Colors
        from src.utils.check.check_folder import folder_problem
        label = _CAT_LABEL.get(category, category)
        current = self.folder(category)
        if first_time:
            print(f"\n{Colors.BOLD}{Colors.HEADER}{'─' * 64}\n📁 PREMIER LANCEMENT {_CAT_TITLE.get(category, category.upper())} : "
                  f"OÙ RANGER LES VIDÉOS ?\n{'─' * 64}{Colors.ENDC}")
            print(f"  Tapez le chemin complet du dossier (celui de la bibliothèque Plex), ex. "
                  f"{_CAT_EXAMPLE.get(category, '/srv/plex/Media')}")
            print(f"  ou Entrée pour {current}. Modifiable ensuite dans Réglages.")
        if f"--{category}-dir" in self.overrides:
            print(f"  {Colors.WARNING}Ce lancement utilise --{category}-dir {self.overrides[f'--{category}-dir']} "
                  f"(start.sh / start.ps1) : il reste prioritaire.{Colors.ENDC}")
        while True:
            typed = _ask(f"Dossier {label} (Entrée = {current}) : ").strip().strip('"\'')
            path = os.path.expanduser(typed) if typed else current
            problem = folder_problem(path)
            if not problem:
                break
            print(f"  {Colors.FAIL}❌ {problem}{Colors.ENDC}")
        self.set_setting(f"{category}_default_dir", path)
        print(f"  {Colors.OKGREEN}✅ Dossier {label} : {path}{Colors.ENDC}")

    # ------------------------------------------------------------- settings
    def settings(self):
        from src.var import Colors
        from src.utils.download import parallel_settings as par
        while True:
            quality = str(self.get_setting("nba_quality") or "1080")
            media_quality = str(self.get_setting("media_quality") or "best")
            print(f"\n{Colors.BOLD}{Colors.HEADER}⚙️  RÉGLAGES{Colors.ENDC}")
            print(f"  1. Dossier des animes          : {self.folder('anime')}")
            print(f"  2. Dossier NBA                 : {self.folder('nba')}")
            print(f"  3. Dossier Films & Séries      : {self.folder('media')}")
            print(f"  4. Anime : morceaux téléchargés en parallèle par épisode : {par.segment_threads()}")
            print(f"  5. Anime : épisodes téléchargés en même temps            : {par.parallel_episodes()}")
            print(f"  6. NBA   : morceaux téléchargés en parallèle             : {self.get_setting('nba_threads') or 32}")
            print(f"  7. NBA   : qualité max                                   : "
                  f"{'la meilleure' if quality == 'best' else quality + 'p'}")
            print(f"  8. Films & Séries : connexions en parallèle par fichier  : {self.get_setting('media_threads') or 16}")
            print(f"  9. Films & Séries : qualité max préférée                 : "
                  f"{'la meilleure' if media_quality == 'best' else media_quality + 'p'}")
            print(" 10. Optimiser/réparer les .mp4 déjà téléchargés (lecture immédiate dans Plex, son cassé)")
            print("  0. Retour")
            choice = _ask("Choix : ").strip()
            if choice in ("", "0", "q"):
                return
            if choice == "1":
                self.ask_folder("anime")
            elif choice == "2":
                self.ask_folder("nba")
            elif choice == "3":
                self.ask_folder("media")
            elif choice in ("4", "5", "6", "8"):
                key, default, top = {"4": ("anime_threads", par.DEFAULT_SEGMENT_THREADS, 64),
                                     "5": ("anime_parallel_episodes", par.DEFAULT_PARALLEL_EPISODES, 8),
                                     "6": ("nba_threads", 32, 128),
                                     "8": ("media_threads", 16, 16)}[choice]
                typed = _ask(f"Nombre (1-{top}, Entrée = {default}) : ").strip() or str(default)
                if typed.isdigit() and 1 <= int(typed) <= top:
                    self.set_setting(key, int(typed))
                else:
                    print(f"  {Colors.FAIL}Nombre invalide.{Colors.ENDC}")
            elif choice == "10":
                from src.utils.mp4_faststart import fix_folder
                for category in ("anime", "nba", "media"):
                    if os.path.isdir(self.folder(category)):
                        fix_folder(self.folder(category))
            elif choice in ("7", "9"):
                key = "nba_quality" if choice == "7" else "media_quality"
                default = "1080" if choice == "7" else "best"
                typed = _ask(f"Qualité max (480/720/1080/1440/2160/best, Entrée = {default}) : ").strip().lower() or default
                if typed in ("480", "720", "1080", "1440", "2160", "best"):
                    self.set_setting(key, typed)
                else:
                    print(f"  {Colors.FAIL}Valeur invalide.{Colors.ENDC}")

    # ----------------------------------------------------------------- menu
    def menu(self):
        from src.var import Colors
        w = 62
        while True:
            print(f"\n{Colors.HEADER}{Colors.BOLD}╔{'═' * w}╗\n║{'ANIME  &  NBA  DOWNLOADER'.center(w)}║\n╚{'═' * w}╝{Colors.ENDC}")
            print(f"  1. 🎌 Anime  {Colors.DIM}(anime-sama, nakanime)   → {self.folder('anime')}{Colors.ENDC}")
            print(f"  2. 🏀 NBA    {Colors.DIM}(basketball-video.com)   → {self.folder('nba')}{Colors.ENDC}")
            print(f"  3. 🎬 Films & Séries {Colors.DIM}(Internet Archive…) → {self.folder('media')}{Colors.ENDC}")
            print(f"  4. ⚙️  Réglages {Colors.DIM}(dossiers, parallélisme, qualité){Colors.ENDC}")
            print("  q. Quitter")
            choice = _ask("Choix : ").strip().lower()
            if choice in ("1", "a", "anime"):
                return "anime"
            if choice in ("2", "n", "nba"):
                return "nba"
            if choice in ("3", "m", "media", "films"):
                return "media"
            if choice == "4":
                self.settings()
            elif choice in ("q", "quit", "exit"):
                sys.exit(0)

    # ------------------------------------------------------------------ run
    def start(self, category, args):
        if not self.configured_dir(category) and sys.stdin.isatty():
            self.ask_folder(category, first_time=True)
        folder = self.folder(category)
        if category in ("nba", "media"):
            if category == "nba":
                from src.nba import app
            else:
                from src.media import app
            if "--default-dir" not in [a.partition("=")[0] for a in args]:
                args = args + ["--default-dir", folder]
            app.run(args)
            sys.exit(0)
        # Anime: continue with the anime downloader in main.py. It builds
        # "<folder>/<anime>/<season>" (see format_save_path) and still asks
        # before downloading, with that path as the default.
        os.environ[ANIME_DIR_ENV] = folder
        sys.argv = [sys.argv[0]] + args
        _exit_on_ctrl_c()


def _ask(prompt):
    from src.var import Colors
    try:
        return input(f"{Colors.BOLD}{prompt}{Colors.ENDC}")
    except EOFError:
        return ""


def _exit_on_ctrl_c():
    """Downloads run in worker threads that a normal exit waits for (the
    download went on after Ctrl+C): stop the whole program at once."""
    def handler(signum, frame):
        print("\nInterrompu.", flush=True)
        os._exit(130)
    try:
        signal.signal(signal.SIGINT, handler)
    except ValueError:  # not in the main thread
        pass


def launch():
    """Called at the top of main.py. Runs the NBA downloader and exits, or
    returns so main.py continues with the anime downloader."""
    if os.name == "nt":
        os.system("")  # ANSI colours in the Windows console
    ensure_requirements()
    overrides, args = _split_args(sys.argv[1:])
    if args[:1] in (["-h"], ["--help"]):
        print(USAGE)
        sys.exit(0)

    launcher = Launcher(overrides)
    set_opts = (("--set-anime-dir", "anime"), ("--set-nba-dir", "nba"), ("--set-media-dir", "media"))
    for opt, category in set_opts:
        if opt in overrides:
            launcher.set_setting(f"{category}_default_dir", os.path.expanduser(overrides[opt]))
            print(f"Dossier {_CAT_LABEL.get(category, category)} : {overrides[opt]}")
    if any(opt in overrides for opt, _ in set_opts):
        sys.exit(0)
    if "--faststart" in overrides:
        from src.utils.mp4_faststart import fix_folder
        fix_folder(os.path.expanduser(overrides["--faststart"]))
        sys.exit(0)

    category = None
    if args and args[0].lower() in ("anime", "nba", "media"):
        category = args.pop(0).lower()
    elif args:
        category = guess_category(args)
    try:
        if category is None:
            category = launcher.menu()
        launcher.start(category, args)
    except KeyboardInterrupt:
        print("\nInterrompu.")
        sys.exit(130)
