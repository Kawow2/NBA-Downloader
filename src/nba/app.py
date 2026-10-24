"""NBA replay downloader for basketball-video.com (Plex-ready .mp4 files).

Started from main.py's Anime/NBA menu (src/launcher.py), or directly:

    python main.py nba                        menu NBA
    python main.py nba --url <page du match>  directement un match
    python main.py nba --debug                enregistre les pages dans ./debug
"""
import argparse
import os
from itertools import islice
import re
import sys

from src.var import Colors, print_status, print_separator
from src.utils.config.config import get_setting, set_setting
from src.utils.check.check_ffmpeg_installed import check_ffmpeg_installed
from src.utils.check.check_folder import folder_problem
from src.nba.site import Site, parse_date, game_matches
from src.nba.plex import plex_target
from src.nba.thumbnail import add_game_thumbnails, thumb_path
from src.nba.downloader import (download_part, merge_parts, ffmpeg_hint, ffmpeg_install_command, configure,
                                SETTINGS, temp_files, estimate_size)
from src.utils.mp4_faststart import ensure_faststart
from src.utils.ffmpeg_progress import media_duration

FALLBACK_DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Videos", "NBA")

# Automatic mode: the latest games are checked BATCH at a time for the
# team, at most MAX_SCANNED of them; and a "game" under MIN_GAME_SIZE is
# not downloaded - a full game is several GB, anything smaller is a
# highlights clip, a recap or a broken upload.
BATCH = 10
MAX_SCANNED = 300
MIN_GAME_SIZE = 1024 ** 3


def banner(site):
    w = 62
    print(f"\n{Colors.HEADER}{Colors.BOLD}╔{'═' * w}╗\n║{'NBA  REPLAY  DOWNLOADER'.center(w)}║\n╚{'═' * w}╝{Colors.ENDC}")
    print(f"  {Colors.DIM}Source : {site.base}   →   .mp4 prêts pour Plex{Colors.ENDC}\n")


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


_default_dir_override = None


def default_dir():
    return _default_dir_override or get_setting("nba_default_dir") or FALLBACK_DEFAULT_DIR


def print_games(games):
    print_separator(title="MATCHS")
    for i, g in enumerate(games, 1):
        d = f"{Colors.OKCYAN}{g.date.strftime('%d/%m/%Y')}{Colors.ENDC}  " if g.date else ""
        print(f"  {Colors.BOLD}{i:>2}.{Colors.ENDC} {d}{g.title}")
    print_separator()


def find_latest_game(site, query):
    """Latest game of a team: the site's 10 latest games are checked, then
    the next 10, and so on; the first batch that has the team gives its
    most recent game. None if the team isn't in the MAX_SCANNED latest
    games. Shared by the automatic menu option and --search."""
    print_status(f"Recherche du dernier match de « {query} » dans les derniers matchs du site...", "loading")
    games = site.iter_latest_games()
    scanned = 0
    while scanned < MAX_SCANNED:
        batch = list(islice(games, BATCH))
        if not batch:
            break
        first, scanned = scanned + 1, scanned + len(batch)
        hits = [g for g in batch if game_matches(g, query)]
        if hits:
            game = site._sort_newest_first(hits)[0]
            when = f" ({game.date.strftime('%d/%m/%Y')})" if game.date else ""
            print_status(f"Dernier match : {game.title}{when} (matchs {first}-{scanned} du site)", "success")
            return game
        print_status(f"Matchs {first}-{scanned} : pas de « {query} ».", "info")
    print_status(f"Aucun match de « {query} » dans les {scanned} derniers matchs du site.", "error")
    return None


def _size_label(size):
    return f"{size / 1024 ** 3:.1f} Go".replace(".", ",") if size >= 1024 ** 3 else f"{size / 1024 ** 2:.0f} Mo"


def estimate_game_size(server, idxs, game):
    """Announced size of the whole game on one server (all its parts),
    before downloading; None when a part's size can't be known up front.
    Stops as soon as the parts seen already reach MIN_GAME_SIZE."""
    total = 0
    for idx in idxs:
        part = server.parts[idx]
        size = estimate_size(part.url, page_url=part.referer or game.url)
        if size is None:
            return None
        total += size
        if total >= MIN_GAME_SIZE:
            break
    return total


def choose_game(site):
    """Returns (url, listed_game, auto). `auto` True means download it
    hands-free (first server, every part, no questions)."""
    while True:
        print(f"{Colors.BOLD}Que voulez-vous faire ?{Colors.ENDC}")
        print("  1. Afficher les 10 derniers matchs du site")
        print("  2. Rechercher un match")
        print("  3. Dernier match d'une équipe (téléchargement automatique)")
        print(f"  {Colors.DIM}(ou collez directement l'URL d'un match — q pour quitter){Colors.ENDC}")
        choice = ask("Choix : ")
        if choice.lower() in ("q", "quit", "exit"):
            return None, None, False
        if choice.startswith("http"):
            return choice, None, False
        if choice == "3":
            query = ask("Équipe (ex : Knicks, Lakers, Celtics) : ")
            if not query:
                continue
            game = find_latest_game(site, query)
            if game:
                return game.url, game, True
            continue
        if choice == "1":
            print_status("Récupération des derniers matchs...", "loading")
            games = site.latest_games(10)
        elif choice == "2":
            query = ask("Recherche (ex : Knicks Spurs, Lakers, Finals Game 5) : ")
            if not query:
                continue
            print_status(f"Recherche de « {query} »...", "loading")
            games = site.search(query, 10)
        else:
            print_status("Tapez 1, 2 ou 3.", "error")
            continue

        if not games:
            print_status("Aucun match trouvé.", "error")
            continue
        print_games(games)
        while True:
            pick = ask(f"Numéro du match (1-{len(games)}, Entrée = retour) : ")
            if not pick:
                break
            if pick.isdigit() and 1 <= int(pick) <= len(games):
                return games[int(pick) - 1].url, games[int(pick) - 1], False
            print_status("Numéro invalide.", "error")


def first_section(servers):
    """Non-interactive counterpart of choose_section: the first game on the
    page (its servers), used by the automatic mode."""
    sections = list(dict.fromkeys(s.section for s in servers if s.section))
    if len(sections) < 2:
        return None, servers
    chosen = sections[0]
    return chosen, [s for s in servers if s.section == chosen]


def choose_section(servers):
    """A page can hold several games (e.g. "USA vs France - FINAL" and
    "Spain vs Germany - 3rd Place"): pick one first."""
    sections = list(dict.fromkeys(s.section for s in servers if s.section))
    if len(sections) < 2:
        return None, servers
    print_separator(title="MATCHS SUR CETTE PAGE")
    for i, sec in enumerate(sections, 1):
        print(f"  {Colors.BOLD}{i:>2}.{Colors.ENDC} {sec}")
    print_separator()
    while True:
        pick = ask(f"Choisir le match (1-{len(sections)}, Entrée = 1) : ") or "1"
        if pick.isdigit() and 1 <= int(pick) <= len(sections):
            chosen = sections[int(pick) - 1]
            return chosen, [s for s in servers if s.section == chosen]
        print_status("Numéro invalide.", "error")


def choose_server(servers):
    print_separator(title="SERVEURS")
    for i, s in enumerate(servers, 1):
        host = f" {Colors.DIM}[{s.host}]{Colors.ENDC}" if s.host != s.name else ""
        n = len(s.parts)
        print(f"  {Colors.BOLD}{i:>2}.{Colors.ENDC} {s.name}{host} — {n} partie{'s' if n > 1 else ''}")
    print_separator()
    while True:
        pick = ask(f"Choisir le serveur (1-{len(servers)}, Entrée = 1) : ") or "1"
        if pick.isdigit() and 1 <= int(pick) <= len(servers):
            return servers[int(pick) - 1]
        print_status("Numéro invalide.", "error")


def parse_parts(text, count):
    text = text.strip().lower()
    if text in ("", "all", "tout", "toutes", "*"):
        return list(range(count))
    picked = []
    # "1-2-3" means parts 1, 2 and 3 (not a range); "," ";" "+" and spaces work too.
    for tok in re.split(r"[\s,;+\-/]+", text):
        if tok.isdigit() and 1 <= int(tok) <= count and int(tok) - 1 not in picked:
            picked.append(int(tok) - 1)
    return picked


def choose_parts(server):
    print_separator(title=f"PARTIES — {server.name}")
    for i, p in enumerate(server.parts, 1):
        print(f"  {Colors.BOLD}{i:>2}.{Colors.ENDC} {p.label}")
    print_separator()
    while True:
        picked = parse_parts(ask("Parties à télécharger (ex : 1-2-3, Entrée = toutes) : "), len(server.parts))
        if picked:
            return picked
        print_status("Sélection invalide.", "error")


def choose_dest(cli_dest):
    if cli_dest:
        return cli_dest
    current = default_dir()
    print(f"\n{Colors.BOLD}{Colors.HEADER}📁 DOSSIER DE DESTINATION{Colors.ENDC}")
    while True:
        typed = ask(f"Chemin (Entrée = {current}) : ").strip('"\'')
        path = os.path.expanduser(typed) if typed else current
        # Checked before downloading: an unwritable Plex folder used to
        # crash the program right after this question.
        problem = folder_problem(path)
        if not problem:
            break
        print_status(problem, "error")
    if not typed:
        return current
    typed = path
    if os.path.abspath(typed) != os.path.abspath(current) and yes("Utiliser ce chemin par défaut les prochaines fois ?", default=False):
        set_setting("nba_default_dir", typed)
        print_status(f"Chemin par défaut enregistré : {typed}", "success")
    return typed


def file_name(game, dest, section, listed, auto=False):
    """Folder and file name for the game: the chosen game's heading on a
    multi-game page, else the title shown in the search/latest list, else
    the page title. When that doesn't identify a game (no date or no
    "A vs B"), ask for a name, suggesting one - except in automatic mode,
    where the suggested name is used as-is without asking."""
    if listed and not game.date and listed.date:
        game.date = listed.date
    title = section or (listed.title if listed else None) or game.title
    folder, stem = plex_target(game, dest, title=title)
    if auto or (game.date and re.search(r"\bvs?\.?\s", title, re.IGNORECASE)):
        return folder, stem
    print(f"\n{Colors.BOLD}{Colors.HEADER}📝 NOM DU FICHIER{Colors.ENDC}")
    print(f"  {Colors.DIM}Proposé : {stem}.mp4{Colors.ENDC}")
    typed = ask("Nom du match (Entrée = nom proposé, ex : Lakers vs Celtics - Game 7) : ")
    if typed:
        custom = type(game)(game.url, typed)
        custom.date = parse_date(typed) or game.date
        folder, stem = plex_target(custom, dest, title=typed)
        if not custom.date:
            day = ask("Date du match (AAAA-MM-JJ, Entrée = aucune) : ")
            custom.date = parse_date(day)
            folder, stem = plex_target(custom, dest, title=typed)
    return folder, stem


def process_game(site, url, cli_dest, listed=None, auto=False):
    print_status("Analyse de la page du match...", "loading")
    game, servers = site.fetch_game(url)
    if not game:
        return
    print(f"\n{Colors.BOLD}{Colors.OKGREEN}🏀 {game.title}{Colors.ENDC}"
          + (f"  {Colors.OKCYAN}({game.date.strftime('%d/%m/%Y')}){Colors.ENDC}" if game.date else ""))
    if not servers:
        print_status("Aucun lecteur vidéo trouvé sur cette page.", "error")
        print_status("Relancez avec --debug et envoyez le contenu du dossier ./debug pour adapter le parseur.", "info")
        return

    if auto:
        # Hands-free: first game on the page, first server, every part, the
        # default folder, the suggested name - no questions.
        section, servers = first_section(servers)
        server = servers[0]
        picked = list(range(len(server.parts)))
        dest = cli_dest or default_dir()
        problem = folder_problem(dest)
        if problem:
            print_status(problem, "error")
            return
        print_status(f"Automatique : serveur « {server.name} », "
                     f"{len(picked)} partie{'s' if len(picked) > 1 else ''}, dossier {dest}", "info")
    else:
        section, servers = choose_section(servers)
        server = choose_server(servers)
        picked = choose_parts(server)
        dest = choose_dest(cli_dest)

    folder, stem = file_name(game, dest, section, listed, auto=auto)
    final_path = os.path.join(folder, stem + ".mp4")
    print_status(f"Fichier : {final_path}", "info")
    if os.path.exists(final_path):
        print_status(f"Déjà présent : {final_path}", "success")
        add_game_thumbnails([final_path])  # games downloaded before thumbnails existed
        return
    can_merge = check_ffmpeg_installed()
    if len(picked) > 1 and not can_merge:
        print_status(f"ffmpeg est nécessaire pour fusionner les parties : {ffmpeg_install_command()}. "
                     "En attendant, elles seront gardées séparément (pt1, pt2...).", "warning")

    # Servers cut the game differently (3 parts on one, 2 on another, the
    # whole game in 1 on a third): part N of one server is not part N of
    # another. So a failed part is never replaced by another server's part
    # - that stitched overlapping pieces into 4-hour files. Instead the whole
    # game is taken from the next server (when every part was asked for).
    attempts = [(server, picked)]
    if len(picked) == len(server.parts):
        attempts += [(s, list(range(len(s.parts)))) for s in servers if s is not server and s.parts]
    done, failed = [], 0
    too_small = 0  # servers set aside in automatic mode: under MIN_GAME_SIZE
    previous_too_small = False
    for k, (srv, idxs) in enumerate(attempts):
        if k:
            print_separator(title=f"SERVEUR SUIVANT : {srv.name}")
            if not previous_too_small:
                print_status(f"Le match est repris en entier depuis « {srv.name} » ({len(srv.parts)} partie(s)) : "
                             "les parties de serveurs différents ne se correspondent pas.", "warning")
        previous_too_small = False
        if auto:
            # Size announced by the server first, so a recap isn't even
            # downloaded; when it can't be known, the downloaded file is
            # checked below instead.
            print_status(f"Vérification de la taille du match sur « {srv.name} »...", "loading")
            size = estimate_game_size(srv, idxs, game)
            if size is not None and size < MIN_GAME_SIZE:
                print_status(f"« {srv.name} » : {_size_label(size)} seulement, moins de 1 Go : sûrement pas le match "
                             "complet (résumé, extraits ?). Pas téléchargé.", "warning")
                done, failed, too_small = [], len(idxs), too_small + 1
                previous_too_small = True
                continue
        done, failed = download_server(srv, idxs, game, folder, stem, final_path, can_merge)
        if auto and not failed:
            total = sum(os.path.getsize(p) for p in done if os.path.exists(p))
            if total < MIN_GAME_SIZE:
                print_status(f"Fichier de {_size_label(total)} seulement, moins de 1 Go : sûrement pas le match "
                             "complet (résumé, extraits ?). Supprimé.", "warning")
                for p in done:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                done, failed, too_small = [], len(idxs), too_small + 1
                previous_too_small = True
                continue
        if not failed:
            break
        if k + 1 < len(attempts):
            print_status(f"{failed} partie(s) impossible(s) à télécharger sur « {srv.name} ».", "error")

    if not failed:
        remove_other_server_parts(folder, stem, keep=done)
        add_game_thumbnails(done)

    print_separator(title="RÉSUMÉ")
    for p in done:
        print(f"  {Colors.OKGREEN}✔{Colors.ENDC} {p}")
    if failed and too_small == len(attempts):
        print(f"  {Colors.FAIL}✘ Aucun serveur ne propose ce match en plus de 1 Go : rien n'a été téléchargé "
              f"(résumé ou extraits plutôt que le match ?).{Colors.ENDC}")
    elif failed:
        print(f"  {Colors.FAIL}✘ {failed} partie(s) en échec{Colors.ENDC}")
        if too_small:
            print_status(f"{too_small} serveur(s) écarté(s) : moins de 1 Go.", "info")
        print_status("Relancez le même match plus tard : les parties déjà téléchargées sont gardées.", "info")
    clean_temp_files(folder, stem, keep_own=bool(failed), ask=not auto)


def _hours(seconds):
    minutes = int(seconds // 60)
    return f"{minutes // 60} h {minutes % 60:02d}"


def _server_tag(server):
    return re.sub(r"[^\w]+", " ", server.name).strip() or "serveur"


def download_server(server, idxs, game, folder, stem, final_path, can_merge):
    """Download the parts idxs of one server, then join them into
    final_path. Returns (files, number of failed parts)."""
    merge = len(idxs) > 1
    tag = _server_tag(server)
    done, failed = [], 0
    SETTINGS["faststart"] = not (merge and can_merge)
    for n, idx in enumerate(idxs, 1):
        part = server.parts[idx]
        # Named after the server: a later run can't mix its parts with
        # another server's (they don't cut the game at the same places).
        out = final_path if len(server.parts) == 1 else os.path.join(folder, f"{stem} [{tag}] - pt{idx + 1}.mp4")
        print_separator(title=f"{server.name} · {part.label} ({n}/{len(idxs)})")
        if os.path.exists(out):
            print_status(f"Déjà présent : {out}", "success")
            done.append(out)
            continue
        if download_part(part.url, out, page_url=part.referer or game.url):
            print_status(f"Enregistré : {out}", "success")
            done.append(out)
        else:
            failed += 1
            print_status(f"Impossible de télécharger {part.label} sur « {server.name} ».", "error")
            if len(idxs) == len(server.parts):
                break  # the whole game will come from another server
    SETTINGS["faststart"] = True

    if failed:
        for p in done:
            ensure_faststart(p)  # kept for a later run: Plex-ready as they are
        return done, failed
    if merge and can_merge:
        durations = [media_duration(p) or 0 for p in done]
        total = sum(durations)
        if total > 3.5 * 3600:
            print_status(f"Attention : {len(done)} parties pour {_hours(total)} au total, anormalement long "
                         "pour un match (parties en double sur ce serveur ?).", "warning")
        if merge_parts(done, final_path):
            for p in done:
                for leftover in (p, thumb_path(p)):  # a part's image from an earlier run
                    try:
                        os.remove(leftover)
                    except OSError:
                        pass
            print_status(f"Match complet : {final_path} ({_hours(total)})", "success")
            return [final_path], 0
        print_status("La fusion a échoué : les parties sont gardées séparément (Plex les regroupe).", "warning")
        for p in done:
            ensure_faststart(p)
    return done, 0


def remove_other_server_parts(folder, stem, keep):
    """Parts left by a server that failed midway (another one then gave
    the whole game): useless once the game is complete."""
    if not os.path.isdir(folder):
        return
    keep = {os.path.abspath(f) for p in keep for f in (p, thumb_path(p))}
    for name in os.listdir(folder):
        path = os.path.abspath(os.path.join(folder, name))
        if name.startswith(stem + " [") and re.search(r"\] - pt\d+\.(mp4|jpg)$", name) and path not in keep:
            try:
                os.remove(path)
            except OSError:
                pass


def clean_temp_files(folder, stem, keep_own=False, ask=True):
    """Delete the game's .part/.ytdl leftovers once it is complete (they
    are kept while it isn't: they let the next run resume), then offer to
    delete leftovers of older, interrupted downloads in the same folder
    (ask=False, automatic mode: only say they're there, keep them)."""
    if not os.path.isdir(folder):
        return
    if not keep_own:
        for p in temp_files(folder, stem):
            try:
                os.remove(p)
            except OSError:
                pass
    others = [p for p in temp_files(folder) if keep_own is False or not os.path.basename(p).startswith(stem)]
    if not others:
        return
    size = sum(os.path.getsize(p) for p in others if os.path.exists(p)) / 1024 ** 3
    print_status(f"{len(others)} fichier(s) temporaire(s) d'anciens téléchargements interrompus ({size:.1f} Go) "
                 f"dans {folder}.", "info")
    if not ask:
        print_status("Gardés (mode automatique) : relancez sans --auto pour choisir de les supprimer.", "info")
        return
    if yes("Les supprimer ? (on ne pourra plus reprendre ces téléchargements)", default=True):
        removed = 0
        for p in others:
            try:
                os.remove(p)
                removed += 1
            except OSError:
                pass
        print_status(f"{removed} fichier(s) supprimé(s).", "success")


def main():
    global _default_dir_override
    parser = argparse.ArgumentParser(prog="main.py nba",
                                     description="Télécharge des matchs NBA depuis basketball-video.com en .mp4 pour Plex.")
    parser.add_argument("--url", help="URL de la page d'un match")
    parser.add_argument("--search", metavar="ÉQUIPE",
                        help="Dernier match d'une équipe (ex : \"Knicks\"), cherché 10 par 10 dans les derniers matchs du site")
    parser.add_argument("--auto", action="store_true",
                        help="Sans rien demander : 1er serveur, toutes les parties, et rien sous 1 Go (un résumé). "
                             "Avec --search (une équipe) ou --url")
    parser.add_argument("--dest", help="Dossier de destination (sinon le chemin par défaut)")
    parser.add_argument("--default-dir", metavar="CHEMIN", help="Chemin proposé par défaut pour cette session (le dossier NBA du menu)")
    parser.add_argument("--set-default-dir", metavar="CHEMIN", help="Définit le chemin par défaut puis quitte")
    parser.add_argument("--site", help="URL du site si le domaine change (mémorisée)")
    parser.add_argument("--quality", choices=["480", "720", "1080", "1440", "2160", "best"],
                        help="Qualité max (défaut 1080, mémorisée). 'best' = la plus haute, souvent ~20 Go par match")
    parser.add_argument("--threads", type=int, metavar="N",
                        help="Morceaux téléchargés en parallèle (défaut 32, mémorisé). Plus = plus rapide, jusqu'à la limite de votre connexion")
    parser.add_argument("--debug", action="store_true", help="Enregistre les pages HTML dans ./debug et affiche les lecteurs détectés")
    args = parser.parse_args()

    if args.set_default_dir:
        set_setting("nba_default_dir", os.path.expanduser(args.set_default_dir))
        print_status(f"Chemin par défaut : {args.set_default_dir}", "success")
        return
    if args.default_dir:
        _default_dir_override = os.path.expanduser(args.default_dir)
    if args.site:
        set_setting("nba_site_url", args.site.rstrip("/"))

    if args.quality:
        set_setting("nba_quality", args.quality)
    if args.threads:
        set_setting("nba_threads", args.threads)
    quality = str(get_setting("nba_quality") or "1080")
    configure(max_height=None if quality == "best" else int(quality),
              threads=get_setting("nba_threads") or 32)

    site = Site(debug=args.debug)
    banner(site)
    print(f"  {Colors.DIM}Chemin par défaut : {default_dir()}{Colors.ENDC}")
    quality_label = f"{SETTINGS['max_height']}p max" if SETTINGS["max_height"] else "la meilleure"
    print(f"  {Colors.DIM}Qualité : {quality_label} · {SETTINGS['threads']} téléchargements en parallèle "
          f"(--quality / --threads pour changer){Colors.ENDC}")
    ffmpeg_hint()
    print()

    if args.url:
        process_game(site, args.url, args.dest, auto=args.auto)
        return

    if args.search:
        game = find_latest_game(site, args.search)
        if not game:
            return
        if args.auto:
            process_game(site, game.url, args.dest, listed=game, auto=True)
            return
        # Without --auto: let the user confirm / pick the parts as usual.
        process_game(site, game.url, args.dest, listed=game)
        return

    while True:
        url, listed, auto = choose_game(site)
        if not url:
            break
        process_game(site, url, args.dest, listed, auto=auto)
        if not yes("\nTélécharger un autre match ?", default=False):
            break
        print()


def run(argv=None):
    """Entry point used by the launcher; argv excludes the program name."""
    if argv is not None:
        sys.argv = [sys.argv[0]] + list(argv)
    try:
        main()
    except Exception:
        # Shown instead of a bare crash (inside tmux the window would
        # close before the error could be read).
        import traceback
        traceback.print_exc()
        print(f"\n{Colors.FAIL}Erreur inattendue : copiez le message ci-dessus pour la signaler.{Colors.ENDC}", flush=True)
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}Interrompu. Relancez le même match pour reprendre le téléchargement "
              f"là où il s'est arrêté.{Colors.ENDC}", flush=True)
        # yt-dlp downloads fragments in worker threads that a normal exit
        # waits for ("Waiting for all threads to shutdown..." while the
        # download keeps going): leave immediately instead. The partial
        # .part/.ytdl files are kept so the next run resumes.
        sys.stderr.flush()
        os._exit(130)
