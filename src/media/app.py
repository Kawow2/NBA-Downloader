"""Films & Séries downloader (Plex-ready files).

Started from main.py's menu (src/launcher.py), or directly:

    python main.py media                       menu Films & Séries
    python main.py media --search "nosferatu"  recherche
    python main.py media --all --search "..."  recherche dans toutes les sources
    python main.py media --source archive --url <id/URL>

The sources live in src/media/sources/ and are fully pluggable: adding one
there makes it appear in the source menu and in the "search everywhere"
option without touching this file.
"""
import argparse
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.var import Colors, print_status, print_separator
from src.utils.config.config import get_setting, set_setting
from src.utils.check.check_folder import folder_problem
from src.media.sources import all_sources, get_source, source_for_url, best_video
from src.media.download import download_video_file
from src.media import naming

FALLBACK_DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Videos", "Films & Séries")

_default_dir_override = None


def default_dir():
    return _default_dir_override or get_setting("media_default_dir") or FALLBACK_DEFAULT_DIR


def _threads():
    try:
        return max(1, min(16, int(get_setting("media_threads", 16))))
    except (TypeError, ValueError):
        return 16


def _max_height():
    q = str(get_setting("media_quality") or "best").lower()
    return None if q == "best" else int(re.sub(r"\D", "", q) or 0) or None


def ask(prompt):
    try:
        return input(f"{Colors.BOLD}{prompt}{Colors.ENDC}").strip()
    except EOFError:
        return ""


def yes(prompt, default=True):
    suffix = " (O/n) : " if default else " (o/N) : "
    ans = ask(prompt + suffix).lower()
    if not ans:
        return default
    return ans in ("o", "oui", "y", "yes", "1")


def banner():
    w = 62
    print(f"\n{Colors.HEADER}{Colors.BOLD}╔{'═' * w}╗\n║{'FILMS  &  SÉRIES  DOWNLOADER'.center(w)}║\n╚{'═' * w}╝{Colors.ENDC}")
    names = ", ".join(s.label for s in all_sources())
    print(f"  {Colors.DIM}Sources : {names}   →   fichiers prêts pour Plex{Colors.ENDC}")
    print(f"  {Colors.DIM}Dossier : {default_dir()}{Colors.ENDC}\n")


# --------------------------------------------------------------- selection
def choose_source():
    """Pick a single source (auto when there is only one)."""
    sources = all_sources()
    if len(sources) == 1:
        print_status(f"Source : {sources[0].label} (seule source disponible pour l'instant).", "info")
        return sources[0]
    print_separator(title="SOURCES")
    for i, s in enumerate(sources, 1):
        print(f"  {Colors.BOLD}{i:>2}.{Colors.ENDC} {s.label}")
    print_separator()
    while True:
        pick = ask(f"Choisir la source (1-{len(sources)}, Entrée = 1) : ") or "1"
        if pick.isdigit() and 1 <= int(pick) <= len(sources):
            return sources[int(pick) - 1]
        print_status("Numéro invalide.", "error")


def search_in(source, query, limit=20):
    print_status(f"Recherche de « {query} » sur {source.label}...", "loading")
    try:
        return source.search(query, limit)
    except Exception as e:
        print_status(f"{source.label} : recherche impossible ({e}).", "error")
        return []


def search_everywhere(query, limit_per=15):
    """Search every source and aggregate the results, newest-looking first
    while keeping each source represented."""
    print_status(f"Recherche de « {query} » dans toutes les sources...", "loading")
    results = []
    sources = all_sources()
    with ThreadPoolExecutor(max_workers=max(1, len(sources))) as pool:
        futures = {pool.submit(src.search, query, limit_per): src for src in sources}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                found = fut.result()
                print_status(f"{src.label} : {len(found)} résultat(s).", "info")
                results.extend(found)
            except Exception as e:
                print_status(f"{src.label} : recherche impossible ({e}).", "error")
    return _dedupe(results)


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _dedupe(results):
    """Merge obvious duplicates across sources (same title + year), keeping
    the first seen, and sort so results read grouped and tidy."""
    seen, unique = set(), []
    for r in results:
        key = (_norm(r.title), r.year)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    unique.sort(key=lambda r: (-(r.year or 0), _norm(r.title)))
    return unique


def print_results(results):
    print_separator(title="RÉSULTATS")
    for i, r in enumerate(results, 1):
        year = f" {Colors.OKCYAN}({r.year}){Colors.ENDC}" if r.year else ""
        tag = f" {Colors.DIM}[{r.source_label}]{Colors.ENDC}"
        print(f"  {Colors.BOLD}{i:>2}.{Colors.ENDC} {r.title}{year}{tag}")
    print_separator()


def choose_result(results):
    while True:
        pick = ask(f"Numéro (1-{len(results)}, Entrée = annuler) : ")
        if not pick:
            return None
        if pick.isdigit() and 1 <= int(pick) <= len(results):
            return results[int(pick) - 1]
        print_status("Numéro invalide.", "error")


def choose_dest(cli_dest):
    if cli_dest:
        return cli_dest
    current = default_dir()
    print(f"\n{Colors.BOLD}{Colors.HEADER}📁 DOSSIER DE DESTINATION{Colors.ENDC}")
    while True:
        typed = ask(f"Chemin (Entrée = {current}) : ").strip('"\'')
        path = os.path.expanduser(typed) if typed else current
        problem = folder_problem(path)
        if not problem:
            break
        print_status(problem, "error")
    if not typed:
        return current
    if os.path.abspath(path) != os.path.abspath(current) and \
            yes("Utiliser ce chemin par défaut les prochaines fois ?", default=False):
        set_setting("media_default_dir", path)
        print_status(f"Chemin par défaut enregistré : {path}", "success")
    return path


def parse_episode_selection(text, count):
    """'1-3,5' / '1 2 3' / 'all' -> sorted 0-based indices (ranges here ARE
    ranges, unlike the NBA parts selector)."""
    text = text.strip().lower()
    if text in ("", "all", "tout", "toutes", "*"):
        return list(range(count))
    picked = []
    for part in re.split(r"[\s,;]+", text):
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            if a.isdigit() and b.isdigit():
                for n in range(int(a), int(b) + 1):
                    if 1 <= n <= count and n - 1 not in picked:
                        picked.append(n - 1)
        elif part.isdigit() and 1 <= int(part) <= count and int(part) - 1 not in picked:
            picked.append(int(part) - 1)
    return sorted(picked)


# ----------------------------------------------------------------- download
def _download_one(video, folder, filename, label):
    out_path = os.path.join(folder, filename)
    print_separator(title=label)
    print_status(f"Fichier : {out_path}", "info")
    if not video:
        print_status("Aucun fichier vidéo disponible.", "error")
        return False
    if video.format or video.height:
        q = video.format + (f" {video.height}p" if video.height else "")
        print_status(f"Qualité : {q.strip()}", "info")
    return download_video_file(video, out_path, threads=_threads())


def download_movie(item, dest):
    video = best_video(item.videos, _max_height())
    folder, filename = naming.movie_target(dest, item.title, item.year, ext=video.ext if video else "mp4")
    ok = _download_one(video, folder, filename, f"FILM · {item.title}")
    print_separator(title="RÉSUMÉ")
    print_status("Terminé." if ok else "Échec du téléchargement.", "success" if ok else "error")


def download_series(item, dest, selection=None):
    eps = item.episodes
    print_separator(title=f"SÉRIE · {item.title}" + (f" ({item.year})" if item.year else ""))
    for i, ep in enumerate(eps, 1):
        title = f" — {ep.title}" if ep.title else ""
        print(f"  {Colors.BOLD}{i:>2}.{Colors.ENDC} S{ep.season:02d}E{ep.number:02d}{title}")
    print_separator()

    if selection is not None:
        indices = parse_episode_selection(selection, len(eps))
    else:
        while True:
            indices = parse_episode_selection(
                ask("Épisodes à télécharger (ex : 1-3,5, Entrée = tous) : "), len(eps))
            if indices:
                break
            print_status("Sélection invalide.", "error")
    if not indices:
        print_status("Aucun épisode sélectionné.", "warning")
        return

    max_h = _max_height()
    done = 0
    # One episode at a time, but each file already spreads over many
    # connections (fast_http) - like the NBA downloader, and it keeps a
    # single clean progress bar instead of several garbled ones.
    for n, idx in enumerate(indices, 1):
        ep = eps[idx]
        video = best_video(ep.videos, max_h)
        folder, filename = naming.series_episode_target(
            dest, item.title, item.year, ep.season, ep.number, ep.title,
            ext=video.ext if video else "mp4")
        label = f"S{ep.season:02d}E{ep.number:02d} ({n}/{len(indices)})"
        if _download_one(video, folder, filename, label):
            done += 1
    print_separator(title="RÉSUMÉ")
    print_status(f"{done}/{len(indices)} épisode(s) téléchargé(s).",
                 "success" if done == len(indices) else "warning")


def process_item(source, result, dest, selection=None):
    print_status("Analyse de l'élément...", "loading")
    try:
        item = source.fetch(result)
    except Exception as e:
        print_status(f"Récupération impossible : {e}", "error")
        return
    if not item:
        return
    if item.kind == "series" and item.episodes:
        download_series(item, dest, selection)
    elif item.videos:
        download_movie(item, dest)
    else:
        print_status("Aucun contenu téléchargeable trouvé.", "error")


# --------------------------------------------------------------------- menu
def interactive_loop(cli_dest, selection):
    while True:
        print(f"{Colors.BOLD}Que voulez-vous faire ?{Colors.ENDC}")
        print("  1. Rechercher dans une source")
        print("  2. Rechercher dans toutes les sources")
        print(f"  {Colors.DIM}(ou collez un identifiant / une URL — q pour quitter){Colors.ENDC}")
        choice = ask("Choix : ")
        if choice.lower() in ("q", "quit", "exit"):
            return

        source = None
        results = []
        if choice.startswith("http") or source_for_url(choice):
            source = source_for_url(choice)
            if not source:
                print_status("Aucune source ne reconnaît cette adresse.", "error")
                continue
            process_item(source, choice, choose_dest(cli_dest), selection)
        elif choice == "1":
            source = choose_source()
            query = ask("Recherche (titre d'un film ou d'une série) : ")
            if not query:
                continue
            results = search_in(source, query)
        elif choice == "2":
            query = ask("Recherche (dans toutes les sources) : ")
            if not query:
                continue
            results = search_everywhere(query)
        else:
            print_status("Tapez 1, 2, ou collez une URL.", "error")
            continue

        if results is not None and (choice in ("1", "2")):
            if not results:
                print_status("Aucun résultat.", "error")
                continue
            print_results(results)
            chosen = choose_result(results)
            if not chosen:
                continue
            # In an aggregated search the result already carries its source.
            picked_source = source if choice == "1" else get_source(chosen.source)
            process_item(picked_source, chosen, choose_dest(cli_dest), selection)

        if not yes("\nTélécharger autre chose ?", default=False):
            return
        print()


def main():
    global _default_dir_override
    parser = argparse.ArgumentParser(prog="main.py media",
                                     description="Télécharge des films et séries (sources enfichables) en fichiers prêts pour Plex.")
    parser.add_argument("--search", help="Terme de recherche")
    parser.add_argument("--all", action="store_true", help="Chercher dans toutes les sources et agréger")
    parser.add_argument("--source", help="Clé ou nom de la source (ex : archive). Par défaut : la première / toutes avec --all")
    parser.add_argument("--url", help="Identifiant ou URL d'un élément à télécharger directement")
    parser.add_argument("--episodes", help="Épisodes d'une série (ex : 1-3,5, 'all')")
    parser.add_argument("--dest", help="Dossier de destination (sinon le chemin par défaut)")
    parser.add_argument("--default-dir", metavar="CHEMIN", help="Chemin proposé par défaut pour cette session (le dossier du menu)")
    parser.add_argument("--set-default-dir", metavar="CHEMIN", help="Définit le chemin par défaut puis quitte")
    parser.add_argument("--threads", type=int, metavar="N", help="Connexions en parallèle par fichier (défaut 16, mémorisé)")
    parser.add_argument("--quality", choices=["480", "720", "1080", "1440", "2160", "best"],
                        help="Qualité max préférée (défaut : la meilleure, mémorisée)")
    parser.add_argument("--list-sources", action="store_true", help="Liste les sources disponibles puis quitte")
    args = parser.parse_args()

    if args.list_sources:
        for s in all_sources():
            print(f"  {s.key:12} {s.label}")
        return
    if args.set_default_dir:
        set_setting("media_default_dir", os.path.expanduser(args.set_default_dir))
        print_status(f"Chemin par défaut : {args.set_default_dir}", "success")
        return
    if args.default_dir:
        _default_dir_override = os.path.expanduser(args.default_dir)
    if args.threads:
        set_setting("media_threads", max(1, min(16, args.threads)))
    if args.quality:
        set_setting("media_quality", args.quality)

    banner()

    # Direct id/URL.
    if args.url:
        source = get_source(args.source) if args.source else source_for_url(args.url)
        if not source:
            source = source_for_url(args.url) or (all_sources()[0] if not args.source else None)
        if not source:
            print_status(f"Source « {args.source} » inconnue.", "error")
            return
        process_item(source, args.url, args.dest or default_dir(), args.episodes)
        return

    # Non-interactive search.
    if args.search:
        if args.all:
            results = search_everywhere(args.search)
        else:
            source = get_source(args.source) if args.source else all_sources()[0]
            if not source:
                print_status(f"Source « {args.source} » inconnue.", "error")
                return
            results = search_in(source, args.search)
        if not results:
            print_status("Aucun résultat.", "error")
            return
        print_results(results)
        chosen = choose_result(results)
        if chosen:
            picked = get_source(chosen.source)
            process_item(picked, chosen, args.dest or choose_dest(None), args.episodes)
        return

    interactive_loop(args.dest, args.episodes)


def run(argv=None):
    """Entry point used by the launcher; argv excludes the program name."""
    if argv is not None:
        sys.argv = [sys.argv[0]] + list(argv)
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}Interrompu.{Colors.ENDC}", flush=True)
        os._exit(130)
    except Exception:
        import traceback
        traceback.print_exc()
        print(f"\n{Colors.FAIL}Erreur inattendue : copiez le message ci-dessus pour la signaler.{Colors.ENDC}", flush=True)
        sys.exit(1)
