"""Téléchargeur de films et de séries (nakios.rent par défaut) en .mp4
rangés pour Plex.

Lancé depuis le menu de main.py (src/launcher.py), ou directement :

    python main.py films                         menu Films & Séries
    python main.py films --search "Inception"    recherche directe
    python main.py films --url https://nakios.rent/series/87108
    python main.py films --url .../series/87108 --season 1 --episodes 1-5
    python main.py films --debug                 enregistre les réponses dans ./debug

Le téléchargement réutilise tout le pipeline du module NBA (extracteurs
voe/uqload/filemoon/vidmoly/sibnet…, yt-dlp, multi-connexions, fusion,
faststart Plex).
"""
import argparse
import os
import re
import sys

from src.var import Colors, print_status, print_separator
from src.utils.config.config import get_setting, set_setting
from src.utils.check.check_ffmpeg_installed import check_ffmpeg_installed
from src.utils.check.check_folder import folder_problem
from src.films.site import Site, parse_media_url
from src.films import plex
from src.nba.downloader import download_part, configure, SETTINGS, ffmpeg_hint, temp_files

FALLBACK_DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Videos", "Films")

_default_dir_override = None


def default_dir():
    return _default_dir_override or get_setting("films_default_dir") or FALLBACK_DEFAULT_DIR


def banner(site):
    w = 62
    print(f"\n{Colors.HEADER}{Colors.BOLD}╔{'═' * w}╗\n║{'FILMS  &  SÉRIES  DOWNLOADER'.center(w)}║\n╚{'═' * w}╝{Colors.ENDC}")
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


def parse_selection(text, available):
    """Transforme '1-5', '1,3,5', 'all'… en liste de numéros présents dans
    `available` (liste triée), en gardant l'ordre demandé."""
    text = (text or "").strip().lower()
    valid = set(available)
    if text in ("", "all", "tout", "toutes", "tous", "*"):
        return list(available)
    picked, seen = [], set()
    for tok in re.split(r"[\s,;+/]+", text):
        if not tok:
            continue
        if "-" in tok:
            a, _, b = tok.partition("-")
            if a.isdigit() and b.isdigit():
                for n in range(int(a), int(b) + 1):
                    if n in valid and n not in seen:
                        seen.add(n)
                        picked.append(n)
            continue
        if tok.isdigit():
            n = int(tok)
            if n in valid and n not in seen:
                seen.add(n)
                picked.append(n)
    return picked


# ---------------------------------------------------------------- session
def ensure_session(site, interactive):
    """S'assure que l'API répond du JSON. Sinon, en interactif, demande le
    cookie de session (+ User-Agent) et réessaie : le site est derrière
    Cloudflare et exige une session (le « credentials: include » du site).
    Le cookie est mémorisé dans config.json (non versionné) ; à refaire
    seulement quand il expire."""
    ok, reason = site.probe_session()
    if ok:
        return True
    if not interactive:
        if reason:
            print_status(reason, "error")
        print_status("Passe le cookie avec --cookie \"...\" (et --user-agent \"...\").", "info")
        return False
    print_status(reason or "L'API exige une session.", "warning")
    print_status(f"{site.host} est derrière Cloudflare et demande une session — à récupérer dans TON "
                 "navigateur (même machine que ce programme) :", "info")
    while True:
        print(f"   {Colors.DIM}1. F12 → onglet Réseau → une requête /api/… → en-tête « cookie » "
              f"(copie TOUTE la valeur).{Colors.ENDC}")
        cookie = ask("Cookie (Entrée = annuler) : ")
        if not cookie:
            return False
        print(f"   {Colors.DIM}2. F12 → Console → tape  navigator.userAgent  et copie le résultat "
              f"(sans les guillemets).{Colors.ENDC}")
        ua = ask("User-Agent (Entrée = garder l'actuel) : ")
        site.set_credentials(cookie=cookie, user_agent=ua or None)
        ok, reason = site.probe_session()
        if ok:
            print_status("Session OK ✅ — mémorisée (à refaire seulement quand elle expirera).", "success")
            return True
        print_status(f"Toujours bloqué : {reason}", "error")
        print_status("Le cookie est lié à ton IP et à ton navigateur et il expire : reprends un cookie "
                     "FRAIS et le User-Agent du MÊME navigateur.", "info")
        if not yes("Réessayer ?", default=True):
            return False


# ---------------------------------------------------------------- recherche
def choose_media(site):
    while True:
        print(f"{Colors.BOLD}Que voulez-vous faire ?{Colors.ENDC}")
        print("  1. Rechercher un film ou une série")
        print(f"  {Colors.DIM}(ou collez directement une URL {site.host} — q pour quitter){Colors.ENDC}")
        choice = ask("Choix : ")
        if choice.lower() in ("q", "quit", "exit"):
            return None
        if choice.startswith("http"):
            parsed = parse_media_url(choice)
            if not parsed:
                print_status("URL non reconnue (attendu .../series/<id> ou .../film/<id>).", "error")
                continue
            from src.films.site import Media
            media_type, media_id = parsed
            return Media({"id": media_id}, media_type=media_type)

        query = choice if choice not in ("1", "") else ask("Recherche (titre du film ou de la série) : ")
        if not query:
            continue
        print_status(f"Recherche de « {query} »...", "loading")
        results = site.search(query)
        if not results:
            print_status("Aucun résultat (ou l'API a refusé la requête).", "error")
            continue
        print_results(results)
        while True:
            pick = ask(f"Numéro (1-{len(results)}, Entrée = nouvelle recherche) : ")
            if not pick:
                break
            if pick.isdigit() and 1 <= int(pick) <= len(results):
                return results[int(pick) - 1]
            print_status("Numéro invalide.", "error")


def print_results(results):
    print_separator(title="RÉSULTATS")
    for i, m in enumerate(results, 1):
        tag = f"{Colors.MAGENTA}Série{Colors.ENDC}" if m.is_series else f"{Colors.OKCYAN}Film{Colors.ENDC}"
        y = f" {Colors.DIM}({m.year}){Colors.ENDC}" if m.year else ""
        print(f"  {Colors.BOLD}{i:>2}.{Colors.ENDC} [{tag}] {m.title}{y}")
    print_separator()


def choose_dest(cli_dest):
    if cli_dest:
        return cli_dest
    current = default_dir()
    print(f"\n{Colors.BOLD}{Colors.HEADER}📁 DOSSIER DE DESTINATION{Colors.ENDC}")
    print(f"  {Colors.DIM}Les films iront dans {os.path.join('<dossier>', plex.FILMS_SUBDIR)}, "
          f"les séries dans {os.path.join('<dossier>', plex.SERIES_SUBDIR)}.{Colors.ENDC}")
    while True:
        typed = ask(f"Chemin (Entrée = {current}) : ").strip('"\'')
        path = os.path.expanduser(typed) if typed else current
        problem = folder_problem(path)
        if not problem:
            break
        print_status(problem, "error")
    if typed and os.path.abspath(path) != os.path.abspath(current) and \
            yes("Utiliser ce chemin par défaut les prochaines fois ?", default=False):
        set_setting("films_default_dir", path)
        print_status(f"Chemin par défaut enregistré : {path}", "success")
    return path


# ---------------------------------------------------------------- download
def _no_sources_help(site, media, season=None, episode=None):
    what = f"S{season:02d}E{episode:02d}" if season else "ce film"
    print_status(f"Aucun lecteur trouvé pour {what}.", "error")
    print_status("L'endpoint des lecteurs est propre au site et n'a pas été deviné. Pour le fixer :", "info")
    print(f"   {Colors.DIM}1. Ouvrez le média sur {site.base} et lancez la lecture (F12 → onglet Réseau).{Colors.ENDC}")
    print(f"   {Colors.DIM}2. Repérez la requête /api/... qui renvoie les lecteurs (voe, uqload, .m3u8…).{Colors.ENDC}")
    print(f"   {Colors.DIM}3. Réglages → « endpoint des lecteurs », en remplaçant l'id par {{id}} "
          f"(et {{season}}/{{episode}} pour les séries).{Colors.ENDC}")
    print(f"   {Colors.DIM}   Relancez aussi avec --debug pour enregistrer les réponses dans ./debug.{Colors.ENDC}")


def download_media(site, media, out_path, season=None, episode=None):
    """Récupère les lecteurs et télécharge le premier qui fonctionne."""
    if os.path.exists(out_path):
        print_status(f"Déjà présent : {out_path}", "success")
        return True
    sources = site.sources(media, season=season, episode=episode)
    if not sources:
        _no_sources_help(site, media, season, episode)
        return False
    print_status(f"{len(sources)} lecteur(s) : {', '.join(dict.fromkeys(s.host for s in sources))}", "info")
    for i, src in enumerate(sources, 1):
        print_separator(title=f"LECTEUR {i}/{len(sources)} · {src.host}")
        if download_part(src.url, out_path, page_url=site.base + "/"):
            print_status(f"Enregistré : {out_path}", "success")
            return True
        print_status(f"Échec sur « {src.host} », lecteur suivant…", "warning")
    print_status("Tous les lecteurs ont échoué.", "error")
    return False


def process_movie(site, media, dest):
    media, _ = site.details(media)
    print(f"\n{Colors.BOLD}{Colors.OKGREEN}🎬 {media.title}{Colors.ENDC}"
          + (f"  {Colors.DIM}({media.year}){Colors.ENDC}" if media.year else ""))
    folder, stem = plex.movie_target(dest, media.title, media.year)
    out_path = os.path.join(folder, stem + ".mp4")
    print_status(f"Fichier : {out_path}", "info")
    ok = download_media(site, media, out_path)
    clean_temp_files(folder, stem, keep_own=not ok)
    return ok


def process_series(site, media, dest, cli_season=None, cli_episodes=None):
    media, seasons = site.details(media)
    print(f"\n{Colors.BOLD}{Colors.OKGREEN}📺 {media.title}{Colors.ENDC}"
          + (f"  {Colors.DIM}({media.year}){Colors.ENDC}" if media.year else ""))
    if not seasons:
        print_status("Impossible de lister les saisons (API différente ?). Relancez avec --debug.", "error")
        return False

    # Choix des saisons
    if cli_season:
        season_numbers = parse_selection(cli_season, [s.number for s in seasons])
    else:
        print_separator(title="SAISONS")
        for s in seasons:
            n = f" — {s.episode_count} épisode(s)" if s.episode_count else ""
            print(f"  {Colors.BOLD}{s.number:>2}.{Colors.ENDC} {s.name or ('Saison ' + str(s.number))}{Colors.DIM}{n}{Colors.ENDC}")
        print_separator()
        season_numbers = parse_selection(
            ask("Saison(s) à télécharger (ex : 1, 1-3, Entrée = toutes) : "), [s.number for s in seasons])
    if not season_numbers:
        print_status("Aucune saison sélectionnée.", "error")
        return False

    failed = 0
    for sn in season_numbers:
        episodes = site.episodes(media, sn)
        if not episodes:
            print_status(f"Saison {sn} : aucun épisode listé.", "error")
            failed += 1
            continue
        nums = [e.number for e in episodes]
        if cli_episodes:
            chosen = parse_selection(cli_episodes, nums)
        elif len(season_numbers) == 1:
            print_separator(title=f"ÉPISODES — SAISON {sn}")
            for e in episodes:
                t = f" — {e.title}" if e.title else ""
                print(f"  {Colors.BOLD}{e.number:>2}.{Colors.ENDC} Épisode {e.number}{Colors.DIM}{t}{Colors.ENDC}")
            print_separator()
            chosen = parse_selection(
                ask(f"Épisode(s) (1-{nums[-1]}, ex : 1-5, Entrée = tous) : "), nums)
        else:
            chosen = nums  # plusieurs saisons d'un coup : tout
        by_num = {e.number: e for e in episodes}
        for en in chosen:
            ep = by_num[en]
            folder, stem = plex.series_target(dest, media.title, sn, en, media.year, ep.title)
            out_path = os.path.join(folder, stem + ".mp4")
            print_separator(title=f"S{sn:02d}E{en:02d}" + (f" · {ep.title}" if ep.title else ""))
            if not download_media(site, media, out_path, season=sn, episode=en):
                failed += 1
            clean_temp_files(folder, stem, keep_own=True)

    print_separator(title="RÉSUMÉ")
    if failed:
        print_status(f"Terminé avec {failed} échec(s). Relancez : les épisodes déjà pris sont gardés.", "warning")
    else:
        print_status("Tous les téléchargements sont terminés ! 🎉", "success")
    return failed == 0


def clean_temp_files(folder, stem, keep_own=False):
    if not os.path.isdir(folder) or keep_own:
        return
    for p in temp_files(folder, stem):
        try:
            os.remove(p)
        except OSError:
            pass


def process(site, media, dest, cli_season=None, cli_episodes=None):
    if media.is_series:
        return process_series(site, media, dest, cli_season, cli_episodes)
    return process_movie(site, media, dest)


# -------------------------------------------------------------------- main
def main():
    global _default_dir_override
    parser = argparse.ArgumentParser(prog="main.py films",
                                     description="Télécharge des films et séries en .mp4 pour Plex.")
    parser.add_argument("--url", help="URL d'un film / d'une série (ex. https://nakios.rent/series/87108)")
    parser.add_argument("--search", help="Recherche directe par titre")
    parser.add_argument("--season", help="Saison(s) pour une série (ex. 1, 1-3, all)")
    parser.add_argument("--episodes", help="Épisode(s) (ex. 1-5, 1,3,5, all)")
    parser.add_argument("--dest", help="Dossier de destination (sinon le chemin par défaut)")
    parser.add_argument("--default-dir", metavar="CHEMIN", help="Chemin par défaut pour cette session (dossier Films du menu)")
    parser.add_argument("--set-default-dir", metavar="CHEMIN", help="Définit le chemin par défaut puis quitte")
    parser.add_argument("--site", help="URL du site si le domaine change (mémorisée)")
    parser.add_argument("--profile-id", help="Valeur de l'en-tête x-profile-id (mémorisée)")
    parser.add_argument("--cookie", help="En-tête Cookie de session, si l'API l'exige (mémorisé)")
    parser.add_argument("--user-agent", help="User-Agent du navigateur ayant obtenu le cookie (mémorisé)")
    parser.add_argument("--sources-path", help="Endpoint des lecteurs, ex. /api/movie/{id}/sources (mémorisé)")
    parser.add_argument("--quality", choices=["480", "720", "1080", "1440", "2160", "best"],
                        help="Qualité max (défaut 1080, mémorisée)")
    parser.add_argument("--threads", type=int, metavar="N", help="Morceaux en parallèle (défaut 32, mémorisé)")
    parser.add_argument("--debug", action="store_true", help="Enregistre les réponses API dans ./debug")
    args = parser.parse_args()

    if args.set_default_dir:
        set_setting("films_default_dir", os.path.expanduser(args.set_default_dir))
        print_status(f"Chemin par défaut : {args.set_default_dir}", "success")
        return
    if args.default_dir:
        _default_dir_override = os.path.expanduser(args.default_dir)
    if args.site:
        set_setting("films_site_url", args.site.rstrip("/"))
    if args.profile_id is not None:
        set_setting("films_profile_id", args.profile_id)
    if args.cookie:
        set_setting("films_cookie", args.cookie)
    if args.user_agent:
        set_setting("films_user_agent", args.user_agent)
    if args.sources_path:
        set_setting("films_sources_path", args.sources_path)
    if args.quality:
        set_setting("films_quality", args.quality)
    if args.threads:
        set_setting("films_threads", args.threads)

    quality = str(get_setting("films_quality") or "1080")
    configure(max_height=None if quality == "best" else int(quality),
              threads=get_setting("films_threads") or 32)

    site = Site(debug=args.debug)
    banner(site)
    print(f"  {Colors.DIM}Chemin par défaut : {default_dir()}{Colors.ENDC}")
    quality_label = f"{SETTINGS['max_height']}p max" if SETTINGS["max_height"] else "la meilleure"
    print(f"  {Colors.DIM}Qualité : {quality_label} · {SETTINGS['threads']} téléchargements en parallèle{Colors.ENDC}")
    ffmpeg_hint()
    print()

    # Le site exige une session (Cloudflare + credentials: include) : vérifier
    # tout de suite, et demander le cookie une fois si besoin.
    if not ensure_session(site, sys.stdin.isatty()):
        print_status("Sans session valide, l'API ne renvoie rien. Relance quand tu as le cookie.", "error")
        return

    # Mode direct (ligne de commande)
    media = None
    if args.url:
        parsed = parse_media_url(args.url)
        if not parsed:
            print_status("URL non reconnue (attendu .../series/<id> ou .../film/<id>).", "error")
            return
        from src.films.site import Media
        media = Media({"id": parsed[1]}, media_type=parsed[0])
    elif args.search:
        results = site.search(args.search)
        if not results:
            print_status("Aucun résultat.", "error")
            return
        print_results(results)
        pick = ask(f"Numéro (1-{len(results)}) : ")
        if not (pick.isdigit() and 1 <= int(pick) <= len(results)):
            return
        media = results[int(pick) - 1]

    if media is not None:
        process(site, media, choose_dest(args.dest), args.season, args.episodes)
        return

    # Mode interactif
    while True:
        media = choose_media(site)
        if media is None:
            break
        process(site, media, choose_dest(args.dest), args.season, args.episodes)
        if not yes("\nTélécharger autre chose ?", default=False):
            break
        print()


def run(argv=None):
    """Point d'entrée utilisé par le lanceur ; argv sans le nom du programme."""
    if argv is not None:
        sys.argv = [sys.argv[0]] + list(argv)
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}Interrompu. Relancez le même média pour reprendre là où ça s'est arrêté.{Colors.ENDC}",
              flush=True)
        sys.stderr.flush()
        os._exit(130)
    except Exception:
        import traceback
        traceback.print_exc()
        print(f"\n{Colors.FAIL}Erreur inattendue : copiez le message ci-dessus pour la signaler.{Colors.ENDC}", flush=True)
        sys.exit(1)
