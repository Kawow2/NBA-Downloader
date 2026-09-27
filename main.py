"""NBA replay downloader for basketball-video.com (Plex-ready .mp4 files).

    python main.py                       menu interactif
    python main.py --url <page du match> directement un match
    python main.py --set-default-dir "D:\\Plex\\NBA"   chemin par défaut mémorisé
    python main.py --debug               enregistre les pages dans ./debug et
                                        affiche les lecteurs détectés
"""
import argparse
import os
import re
import sys

if os.name == "nt":
    os.system("")  # enables ANSI colours in the Windows console


def ensure_requirements():
    """Install requirements.txt into the running Python (the venv when
    launched by start.ps1) if a module is missing, e.g. yt-dlp after an
    update added it."""
    import importlib.util
    # curl_cffi: lets yt-dlp impersonate a browser (required by Dailymotion)
    modules = ("requests", "bs4", "tqdm", "yt_dlp", "curl_cffi", "Crypto", "av", "cloudscraper")
    missing = [m for m in modules if importlib.util.find_spec(m) is None]
    if not missing:
        return
    import subprocess
    print(f"Modules manquants : {', '.join(missing)} — installation dans {sys.executable} ...")
    req = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
    subprocess.call([sys.executable, "-m", "pip", "install", "-r", req])
    importlib.invalidate_caches()
    still = [m for m in modules if importlib.util.find_spec(m) is None]
    if still:
        print(f"Toujours manquants : {', '.join(still)}. Lancez : \"{sys.executable}\" -m pip install -r requirements.txt")
        if any(m in still for m in ("requests", "bs4", "tqdm")):
            sys.exit(1)


ensure_requirements()

from src.var import Colors, print_status, print_separator
from src.utils.config.config import get_setting, set_setting
from src.utils.check.check_ffmpeg_installed import check_ffmpeg_installed
from src.nba.site import Site
from src.nba.plex import plex_target
from src.nba.downloader import download_part, merge_parts, ffmpeg_hint, ffmpeg_install_command, configure, SETTINGS

FALLBACK_DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Videos", "NBA")


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


def choose_game(site):
    while True:
        print(f"{Colors.BOLD}Que voulez-vous faire ?{Colors.ENDC}")
        print("  1. Afficher les 10 derniers matchs du site")
        print("  2. Rechercher un match")
        print(f"  {Colors.DIM}(ou collez directement l'URL d'un match — q pour quitter){Colors.ENDC}")
        choice = ask("Choix : ")
        if choice.lower() in ("q", "quit", "exit"):
            return None
        if choice.startswith("http"):
            return choice
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
            print_status("Tapez 1 ou 2.", "error")
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
                return games[int(pick) - 1].url
            print_status("Numéro invalide.", "error")


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
        picked = parse_parts(ask(f"Parties à télécharger (ex : 1-2-3, Entrée = toutes) : "), len(server.parts))
        if picked:
            return picked
        print_status("Sélection invalide.", "error")


def choose_dest(cli_dest):
    if cli_dest:
        return cli_dest
    current = default_dir()
    print(f"\n{Colors.BOLD}{Colors.HEADER}📁 DOSSIER DE DESTINATION{Colors.ENDC}")
    typed = ask(f"Chemin (Entrée = {current}) : ").strip('"\'')
    if not typed:
        return current
    typed = os.path.expanduser(typed)
    if os.path.abspath(typed) != os.path.abspath(current) and yes("Utiliser ce chemin par défaut les prochaines fois ?", default=False):
        set_setting("nba_default_dir", typed)
        print_status(f"Chemin par défaut enregistré : {typed}", "success")
    return typed


def alternatives(servers, chosen, part_index):
    """Same part on the other servers, used if the chosen one fails."""
    part = chosen.parts[part_index]
    alts = []
    for s in servers:
        if s is chosen:
            continue
        same = [p for p in s.parts if p.number == part.number]
        if same:
            alts.append((s, same[0]))
        elif len(s.parts) == len(chosen.parts):
            alts.append((s, s.parts[part_index]))
    return alts


def process_game(site, url, cli_dest):
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

    section, servers = choose_section(servers)
    server = choose_server(servers)
    picked = choose_parts(server)
    dest = choose_dest(cli_dest)

    folder, stem = plex_target(game, dest, title=section)
    single = len(server.parts) == 1
    # Several parts are always joined into one file once all are downloaded.
    merge = len(picked) > 1
    if merge and not check_ffmpeg_installed():
        print_status(f"ffmpeg est nécessaire pour fusionner les parties : {ffmpeg_install_command()}. "
                     "En attendant, elles seront gardées séparément (pt1, pt2...).", "warning")

    final_path = os.path.join(folder, stem + ".mp4")
    if merge and os.path.exists(final_path):
        print_status(f"Déjà présent : {final_path}", "success")
        return

    done, failed = [], 0
    for n, idx in enumerate(picked, 1):
        part = server.parts[idx]
        out = final_path if single else os.path.join(folder, f"{stem} - pt{idx + 1}.mp4")
        print_separator(title=f"{part.label} ({n}/{len(picked)})")
        if os.path.exists(out):
            print_status(f"Déjà présent : {out}", "success")
            done.append(out)
            continue
        ok = download_part(part.url, out, page_url=part.referer or game.url)
        if not ok:
            for alt_server, alt_part in alternatives(servers, server, idx):
                print_status(f"Nouvel essai sur le serveur « {alt_server.name} »...", "warning")
                if download_part(alt_part.url, out, page_url=alt_part.referer or game.url):
                    ok = True
                    break
        if ok:
            print_status(f"Enregistré : {out}", "success")
            done.append(out)
        else:
            failed += 1
            print_status(f"Impossible de télécharger {part.label}.", "error")

    if merge and not failed and check_ffmpeg_installed():
        if merge_parts(done, final_path):
            for p in done:
                try:
                    os.remove(p)
                except OSError:
                    pass
            done = [final_path]
            print_status(f"Match complet : {final_path}", "success")
        else:
            print_status("La fusion a échoué : les parties sont gardées séparément (Plex les regroupe).", "warning")
    elif merge and failed:
        print_status("Toutes les parties n'ont pas été téléchargées : pas de fusion. Relancez le même match : "
                     "les parties déjà là sont gardées, puis tout est fusionné.", "warning")

    print_separator(title="RÉSUMÉ")
    for p in done:
        print(f"  {Colors.OKGREEN}✔{Colors.ENDC} {p}")
    if failed:
        print(f"  {Colors.FAIL}✘ {failed} partie(s) en échec{Colors.ENDC}")


def main():
    global _default_dir_override
    parser = argparse.ArgumentParser(description="Télécharge des matchs NBA depuis basketball-video.com en .mp4 pour Plex.")
    parser.add_argument("--url", help="URL de la page d'un match")
    parser.add_argument("--dest", help="Dossier de destination (sinon le chemin par défaut)")
    parser.add_argument("--default-dir", metavar="CHEMIN", help="Chemin proposé par défaut pour cette session (utilisé par NBA-Downloader.ps1)")
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
        process_game(site, args.url, args.dest)
        return

    while True:
        url = choose_game(site)
        if not url:
            break
        process_game(site, url, args.dest)
        if not yes("\nTélécharger un autre match ?", default=False):
            break
        print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}Interrompu. Relancez le même match pour reprendre le téléchargement "
              f"là où il s'est arrêté.{Colors.ENDC}", flush=True)
        # yt-dlp downloads fragments in worker threads that a normal exit
        # waits for ("Waiting for all threads to shutdown..." while the
        # download keeps going): leave immediately instead. The partial
        # .part/.ytdl files are kept so the next run resumes.
        sys.stderr.flush()
        os._exit(130)
