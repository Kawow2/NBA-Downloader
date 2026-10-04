"""Téléchargeur de films et de séries (nakios.rent par défaut) en .mp4
rangés pour Plex.

Lancé depuis le menu de main.py (src/launcher.py), ou directement :

    python main.py films                         menu Films & Séries
    python main.py films --search "Inception"    recherche directe
    python main.py films --url https://nakios.rent/series/87108
    python main.py films --url .../series/87108 --season 1 --episodes 1-5
    python main.py films --export-session         (PC avec navigateur) imprime un jeton de session
    python main.py films --import-session JETON   (serveur SSH) importe ce jeton
    python main.py films --debug                 enregistre les réponses dans ./debug

Le téléchargement réutilise tout le pipeline du module NBA (extracteurs
voe/uqload/filemoon/vidmoly/sibnet…, yt-dlp, multi-connexions, fusion,
faststart Plex).
"""
import argparse
import base64
import json
import os
import re
import sys

import requests
from tqdm import tqdm

from src.var import Colors, DEFAULT_USER_AGENT, print_status, print_separator
from src.utils.config.config import get_setting, set_setting
from src.utils.check.check_ffmpeg_installed import check_ffmpeg_installed
from src.utils.check.check_folder import folder_problem
from src.utils.mp4_faststart import ensure_faststart
from src.films.site import Site, parse_media_url
from src.films import plex
from src.nba import fast_http
from src.nba.downloader import download_part, configure, SETTINGS, ffmpeg_hint, temp_files

_VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".m4v", ".webm", ".mov")


def _already_downloaded(out_path):
    """Chemin d'un fichier vidéo déjà présent pour ce média (toute extension),
    ou None. Évite de retélécharger un .mkv (débrideur) nommé en .mp4."""
    base = os.path.splitext(out_path)[0]
    for ext in _VIDEO_EXTS:
        if os.path.exists(base + ext):
            return base + ext
    return None

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
BROWSER_HINT = "pip install playwright && playwright install chromium"


def ensure_session(site, interactive, use_browser=True, browser_visible=False, profile_dir=None):
    """S'assure que l'API répond du JSON, dans cet ordre :
      1. test direct (cookie déjà mémorisé, encore valide) ;
      2. récupération AUTOMATIQUE via un navigateur headless (Playwright) qui
         passe Cloudflare tout seul — aucun cookie à coller ;
      3. en dernier recours (interactif), saisie manuelle du cookie.
    Le cookie obtenu est mémorisé (config.json, non versionné) et, comme ce
    test tourne à chaque lancement, il est renouvelé automatiquement quand il
    expire."""
    ok, reason = site.probe_session()
    if ok:
        return True

    # 2) Automatique : un vrai navigateur (Playwright) franchit Cloudflare.
    if use_browser:
        from src.films import session as browser_session
        if not browser_session.available() and interactive:
            if yes("Installer Playwright pour récupérer la session automatiquement "
                   "(télécharge un navigateur, ~1 min, une seule fois) ?", default=True):
                browser_session.install(status=print_status)
        if browser_session.available():
            bs = browser_session.BrowserSession(site.base, status=print_status, profile_dir=profile_dir)
            if bs.open(headless=not browser_visible):
                # Capturer les en-têtes propres à l'appli (x-profile-id…) vus
                # sur un vrai appel : sans eux l'API renvoie du HTML.
                api_base = getattr(bs, "api_base", None)
                if api_base:
                    set_setting("films_api_base", api_base)
                captured = getattr(bs, "api_headers", None) or {}
                if captured:
                    pid = next((v for k, v in captured.items() if k.lower() == "x-profile-id"), None)
                    if pid is not None:
                        set_setting("films_profile_id", pid)
                    extra = {k: v for k, v in captured.items() if k.lower() != "x-profile-id"}
                    set_setting("films_extra_headers", json.dumps(extra))
                # a) réutiliser cookie + en-têtes avec le client rapide (curl_cffi).
                cookie, ua = bs.cookies_and_ua()
                if cookie:
                    site.set_credentials(cookie=cookie, user_agent=ua)
                else:
                    site.session = site._make_session()  # relire profile-id / extra-headers
                ok, _ = site.probe_session()
                if ok:
                    bs.close()
                    print_status("Session OK ✅ (client rapide, mémorisée).", "success")
                    return True
                # b) sinon, garder le navigateur comme transport de l'API.
                site.attach_browser(bs)
                ok, _ = site.probe_session()
                if ok:
                    print_status("Session OK ✅ via navigateur (laissé ouvert le temps de la session).",
                                 "success")
                    return True
                summary = bs.observed_summary() if hasattr(bs, "observed_summary") else ""
                site.browser = None
                bs.close()
                print_status("Le navigateur a franchi Cloudflare mais mes appels API ne renvoient pas de JSON.", "warning")
                if summary:
                    print_status("Appels API réellement utilisés par le site (copie-moi ces lignes) :", "info")
                    print(summary)
            else:
                print_status("Navigateur : Cloudflare non franchi. Sur un PC avec écran : --browser-visible ; "
                             "sur un serveur sans écran : sudo apt install xvfb && pip install pyvirtualdisplay "
                             "(sinon, repli manuel ci-dessous).", "warning")
        elif interactive:
            print_status(f"Astuce : installez Playwright pour automatiser la session ({BROWSER_HINT}).", "info")

    # 3) Manuel (dernier recours).
    if not interactive:
        if reason:
            print_status(reason, "error")
        print_status(f"Passe --cookie \"...\" (+ --user-agent \"...\"), ou installe Playwright pour "
                     f"tout automatiser ({BROWSER_HINT}).", "info")
        return False
    print_status(reason or "L'API exige une session.", "warning")
    print_status(f"{site.host} est derrière Cloudflare — récupère la session dans TON navigateur "
                 "(même machine) :", "info")
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
            print_status("Session OK ✅ — mémorisée.", "success")
            return True
        print_status(f"Toujours bloqué : {reason}", "error")
        print_status("Le cookie est lié à ton IP et à ton navigateur et il expire : reprends un cookie "
                     "FRAIS et le User-Agent du MÊME navigateur.", "info")
        if not yes("Réessayer ?", default=True):
            return False


# ------------------------------------------------------------ fournisseurs
class NakiosProvider:
    """Fournisseur streaming nakios (enveloppe src/films/site.Site)."""
    name = "nakios"
    label = "Nakios"
    debrid = None

    def __init__(self, site):
        self.site = site
        self.base = site.base

    def search(self, query):
        res = self.site.search(query)
        for m in res:
            m.provider = self
        return res

    def details(self, media):
        return self.site.details(media)

    def episodes(self, media, season):
        return self.site.episodes(media, season)

    def sources(self, media, season=None, episode=None):
        return self.site.sources(media, season=season, episode=episode)


def build_providers(site, nakios_ready):
    """Fournisseurs actifs : nakios (si session OK) + zone-telechargement (si
    une clé AllDebrid est configurée)."""
    providers = []
    if nakios_ready:
        providers.append(NakiosProvider(site))
    if get_setting("films_alldebrid_apikey"):
        from src.films.zonetelechargement import Zone
        from src.films.alldebrid import AllDebrid
        providers.append(Zone(debrid=AllDebrid()))
    return providers


def search_all(providers, query, per_source=10):
    """Recherche agrégée : au plus `per_source` résultats par fournisseur
    (10 par défaut, soit ~20 au total avec deux sites), regroupés par source
    dans l'ordre des fournisseurs."""
    out = []
    for prov in providers:
        try:
            res = prov.search(query) or []
        except Exception as e:
            print_status(f"{prov.label} : recherche impossible ({str(e)[:80]})", "warning")
            continue
        out.extend(res[:per_source])
    return out


def media_from_url(url, providers):
    """Reconnaît une URL collée (nakios .../series|film/<id>, ou zone
    .../?p=film&id=…) et renvoie un média rattaché au bon fournisseur."""
    from src.films.site import Media
    parsed = parse_media_url(url)
    nak = next((p for p in providers if p.name == "nakios"), None)
    if parsed and nak:
        m = Media({"id": parsed[1]}, media_type=parsed[0])
        m.provider = nak
        return m
    mz = re.search(r'[?&]p=film&id=(\d+)-([a-z0-9\-]+)', url, re.I)
    zone = next((p for p in providers if p.name == "zone"), None)
    if mz and zone:
        m = Media({"id": mz.group(1), "title": re.sub(r"[-_]+", " ", mz.group(2)).title()}, media_type="movie")
        m.provider = zone
        m.url = f"/?p=film&id={mz.group(1)}-{mz.group(2)}"
        return m
    return None


# ---------------------------------------------------------------- recherche
def choose_media(providers):
    while True:
        print(f"{Colors.BOLD}Que voulez-vous faire ?{Colors.ENDC}")
        print("  1. Rechercher un film ou une série")
        print(f"  {Colors.DIM}(ou collez une URL d'un des sites — q pour quitter){Colors.ENDC}")
        choice = ask("Choix : ")
        if choice.lower() in ("q", "quit", "exit"):
            return None
        if choice.startswith("http"):
            media = media_from_url(choice, providers)
            if media:
                return media
            print_status("URL non reconnue.", "error")
            continue

        query = choice if choice not in ("1", "") else ask("Recherche (titre du film ou de la série) : ")
        if not query:
            continue
        print_status(f"Recherche de « {query} » sur {len(providers)} site(s)...", "loading")
        results = search_all(providers, query)
        if not results:
            print_status("Aucun résultat.", "error")
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
    """Affiche les résultats regroupés par source (un en-tête par site), en
    gardant une numérotation globale 1..N pour la sélection."""
    print_separator(title="RÉSULTATS")
    counts = {}
    for m in results:
        p = getattr(m, "provider", None)
        counts[id(p)] = counts.get(id(p), 0) + 1
    last = object()
    for i, m in enumerate(results, 1):
        prov_obj = getattr(m, "provider", None)
        if prov_obj is not last:
            last = prov_obj
            label = getattr(prov_obj, "label", "") or "Source"
            print(f"\n  {Colors.HEADER}{Colors.BOLD}— {label} ({counts.get(id(prov_obj), 0)}) —{Colors.ENDC}")
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
def _no_sources_help(provider, media, season=None, episode=None):
    what = f"S{season:02d}E{episode:02d}" if season else "ce film"
    print_status(f"Aucun lecteur trouvé pour {what}.", "error")
    if getattr(provider, "name", None) != "nakios":
        print_status(f"Aucun lien de téléchargement exploitable sur cette page ({provider.base}).", "info")
        return
    print_status("L'endpoint des lecteurs est propre au site et n'a pas été deviné. Pour le fixer :", "info")
    print(f"   {Colors.DIM}1. Ouvrez le média sur {provider.base} et lancez la lecture (F12 → onglet Réseau).{Colors.ENDC}")
    print(f"   {Colors.DIM}2. Repérez la requête /api/... qui renvoie les lecteurs (voe, uqload, .m3u8…).{Colors.ENDC}")
    print(f"   {Colors.DIM}3. Réglages → « endpoint des lecteurs », en remplaçant l'id par {{id}} "
          f"(et {{season}}/{{episode}} pour les séries).{Colors.ENDC}")
    print(f"   {Colors.DIM}   Relancez aussi avec --debug pour enregistrer les réponses dans ./debug.{Colors.ENDC}")


def choose_source(sources, interactive):
    """Propose les lecteurs / qualités disponibles. Renvoie le lecteur choisi,
    ou None = auto (meilleure qualité, les autres en secours). Ne demande rien
    s'il n'y a qu'un seul lecteur (souvent un seul fichier par titre)."""
    if not sources or len(sources) == 1 or not interactive:
        return None
    print(f"\n{Colors.BOLD}{Colors.HEADER}🎥  LECTEURS / QUALITÉS DISPONIBLES{Colors.ENDC}")
    for i, s in enumerate(sources, 1):
        print(f"  {Colors.BOLD}{i}.{Colors.ENDC} {s.describe()}")
    print(f"  {Colors.BOLD}0.{Colors.ENDC} Auto {Colors.DIM}(meilleure qualité, les autres en secours){Colors.ENDC}")
    while True:
        pick = ask("Choix du lecteur (0 = auto) : ").strip()
        if pick in ("", "0"):
            return None
        if pick.isdigit() and 1 <= int(pick) <= len(sources):
            return sources[int(pick) - 1]
        print_status("Choix invalide.", "error")


def _ordered_sources(sources, prefer):
    """Lecteurs ordonnés pour l'essai : le préféré (même hébergeur / qualité /
    langue) d'abord, sinon par qualité décroissante."""
    if not prefer:
        return list(sources)  # site.sources() renvoie déjà meilleure qualité d'abord
    def score(s):
        sc = 0
        if s.host == prefer.host:
            sc += 1000
        if prefer.height and s.height == prefer.height:
            sc += 400
        if prefer.lang and s.lang == prefer.lang:
            sc += 200
        return sc + (s.height or 0)
    return sorted(sources, key=score, reverse=True)


# ----------------------------------------------------- téléchargement débridé
_ARCHIVE_RX = re.compile(r"\.(rar|zip|7z|r\d{2}|\d{3}|part\d+\.rar|tar|gz|bz2)$", re.I)


def _is_archive(name):
    return bool(_ARCHIVE_RX.search(name or ""))


def _debrid_out_path(out_path, filename):
    """Garde le nom rangé pour Plex, mais l'extension réelle du fichier débridé
    (souvent .mkv en 4K/BluRay)."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in _VIDEO_EXTS:
        return os.path.splitext(out_path)[0] + ext
    return out_path


def _fetch_simple(url, tmp, headers, size=None):
    """Repli : téléchargement en une seule connexion (hébergeur refusant les
    requêtes par plage)."""
    try:
        with requests.get(url, headers=headers, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = size or int(r.headers.get("Content-Length") or 0)
            bar = tqdm(total=total or None, unit="B", unit_scale=True, unit_divisor=1024,
                       desc="📥 Téléchargement")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=512 * 1024):
                    if chunk:
                        f.write(chunk)
                        bar.update(len(chunk))
            bar.close()
        return True
    except (requests.RequestException, OSError) as e:
        print_status(f"Téléchargement direct échoué : {str(e)[:120]}", "error")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _fetch_direct(url, out_path, size=None):
    """Télécharge un lien direct (débrideur) : multi-connexions, repli sur une
    connexion simple ; faststart Plex pour les .mp4. True si le fichier est en
    place."""
    tmp = out_path + ".part"
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    label = "📥 " + os.path.basename(out_path)
    if not fast_http.download(url, tmp, headers, SETTINGS["threads"], label=label):
        if not _fetch_simple(url, tmp, headers, size):
            return False
    try:
        os.replace(tmp, out_path)
    except OSError as e:
        print_status(f"Impossible de finaliser le fichier ({e}).", "error")
        return False
    if out_path.lower().endswith(".mp4") and SETTINGS.get("faststart", True):
        ensure_faststart(out_path)
    return True


def _download_via_debrid(provider, src, out_path):
    """Source « téléchargement » (zone + débrideur) : récupère les liens
    dl-protect de la page, débloque le premier qui marche via le débrideur,
    puis télécharge le fichier direct. Renvoie le chemin final (avec l'extension
    réelle) ou None."""
    debrid = getattr(provider, "debrid", None)
    if debrid is None or not getattr(debrid, "configured", False):
        print_status("Aucune clé AllDebrid configurée : impossible de débrider ce lien "
                     "(relance avec --alldebrid-key \"VOTRE_CLÉ\").", "error")
        return None
    links = provider.download_links(getattr(src, "url", None))
    if not links:
        print_status("Aucun lien de téléchargement trouvé sur la page.", "error")
        return None
    last_err = None
    for link in links:
        try:
            direct, filename, size = debrid.resolve(link)
        except Exception as e:
            last_err = str(e)[:160]
            continue
        final = _debrid_out_path(out_path, filename)
        existing = _already_downloaded(final)
        if existing:
            print_status(f"Déjà présent : {existing}", "success")
            return existing
        if _is_archive(filename):
            print_status(f"« {filename} » est une archive (rar/zip…) ; la décompression "
                         "automatique n'est pas encore gérée — je la récupère telle quelle.", "warning")
        gb = f" ({int(size) / 1024 ** 3:.2f} Go)" if size else ""
        print_status(f"Lien débridé : {filename or 'fichier'}{gb}", "success")
        if _fetch_direct(direct, final, size=int(size) if size else None):
            return final
        last_err = "téléchargement du lien direct échoué"
    if last_err:
        print_status(f"Déblocage impossible : {last_err}", "error")
    return None


def download_media(provider, media, out_path, season=None, episode=None, prefer=None, sources=None):
    """Télécharge le premier lecteur qui fonctionne (ordonné selon `prefer`).
    Les sources « téléchargement » (zone) passent par le débrideur."""
    existing = _already_downloaded(out_path)
    if existing:
        print_status(f"Déjà présent : {existing}", "success")
        return True
    if sources is None:
        sources = provider.sources(media, season=season, episode=episode)
    if not sources:
        _no_sources_help(provider, media, season, episode)
        return False
    ordered = _ordered_sources(sources, prefer)
    for i, src in enumerate(ordered, 1):
        print_separator(title=f"LECTEUR {i}/{len(ordered)} · {src.describe()}")
        if getattr(src, "needs_debrid", False):
            final = _download_via_debrid(provider, src, out_path)
            if final:
                print_status(f"Enregistré : {final}", "success")
                return True
        elif download_part(src.url, out_path, page_url=provider.base + "/"):
            print_status(f"Enregistré : {out_path}", "success")
            return True
        print_status(f"Échec sur « {src.describe()} », lecteur suivant…", "warning")
    print_status("Tous les lecteurs ont échoué.", "error")
    return False


def process_movie(provider, media, dest):
    media, _ = provider.details(media)
    print(f"\n{Colors.BOLD}{Colors.OKGREEN}🎬 {media.title}{Colors.ENDC}"
          + (f"  {Colors.DIM}({media.year}){Colors.ENDC}" if media.year else ""))
    folder, stem = plex.movie_target(dest, media.title, media.year)
    out_path = os.path.join(folder, stem + ".mp4")
    print_status(f"Fichier : {out_path}", "info")
    existing = _already_downloaded(out_path)
    if existing:
        print_status(f"Déjà présent : {existing}", "success")
        return True
    sources = provider.sources(media)
    if not sources:
        _no_sources_help(provider, media)
        clean_temp_files(folder, stem, keep_own=True)
        return False
    prefer = choose_source(sources, sys.stdin.isatty())
    ok = download_media(provider, media, out_path, prefer=prefer, sources=sources)
    clean_temp_files(folder, stem, keep_own=not ok)
    return ok


def process_series(provider, media, dest, cli_season=None, cli_episodes=None):
    media, seasons = provider.details(media)
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
    prefer = None          # lecteur préféré, choisi une seule fois
    asked_source = False
    for sn in season_numbers:
        episodes = provider.episodes(media, sn)
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
            existing = _already_downloaded(out_path)
            if existing:
                print_status(f"Déjà présent : {existing}", "success")
                continue
            print_separator(title=f"S{sn:02d}E{en:02d}" + (f" · {ep.title}" if ep.title else ""))
            # Choix du lecteur/qualité une seule fois (sur le 1er épisode à
            # télécharger), puis appliqué à tous les suivants.
            ep_sources = None
            if not asked_source:
                ep_sources = provider.sources(media, season=sn, episode=en)
                prefer = choose_source(ep_sources, sys.stdin.isatty())
                asked_source = True
            if not download_media(provider, media, out_path, season=sn, episode=en,
                                  prefer=prefer, sources=ep_sources):
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


def process(provider, media, dest, cli_season=None, cli_episodes=None):
    if media.is_series:
        return process_series(provider, media, dest, cli_season, cli_episodes)
    return process_movie(provider, media, dest)


# ----------------------------------------------------- session SSH / headless
# Le serveur Plex (portable Linux en SSH) n'a pas de navigateur. On capture la
# session sur une machine QUI EN A UN (le PC fixe, même IP publique que le
# serveur, donc le cf_clearance y est valable), puis on la transfère via un
# jeton à coller sur le serveur.
_SESSION_KEYS = ("films_cookie", "films_user_agent", "films_api_base",
                 "films_profile_id", "films_extra_headers")


def export_session(browser_visible, profile_dir=None):
    """(sur une machine avec navigateur) Récupère la session via le navigateur
    et renvoie un jeton base64 contenant tout ce qu'il faut (cookie, UA, base
    d'API, profil, en-têtes), ou None."""
    from src.films import session as browser_session
    if not browser_session.available():
        if sys.stdin.isatty() and yes("Playwright est requis pour capturer la session. L'installer "
                                       "maintenant (~1 min) ?", default=True):
            browser_session.install(status=print_status)
    if not browser_session.available():
        print_status("Playwright indisponible : " + browser_session.install_hint(), "error")
        return None
    site = Site()
    bs = browser_session.BrowserSession(site.base, status=print_status, profile_dir=profile_dir)
    if not bs.open(headless=not browser_visible):
        bs.close()
        print_status("Cloudflare non franchi. Sur un PC avec écran, réessaie avec --browser-visible.", "error")
        return None
    cookie, ua = bs.cookies_and_ua()
    headers = getattr(bs, "api_headers", None) or {}
    api_base = getattr(bs, "api_base", None)
    bs.close()
    pid = next((v for k, v in headers.items() if k.lower() == "x-profile-id"), None)
    extra = {k: v for k, v in headers.items() if k.lower() != "x-profile-id"}
    bundle = {"films_cookie": cookie, "films_user_agent": ua, "films_api_base": api_base,
              "films_profile_id": pid, "films_extra_headers": json.dumps(extra) if extra else None,
              "site": site.base}
    return base64.urlsafe_b64encode(json.dumps(bundle).encode()).decode()


def import_session(token):
    """(sur le serveur SSH) Importe le jeton généré par --export-session."""
    try:
        raw = base64.urlsafe_b64decode(token.strip().encode())
        bundle = json.loads(raw.decode())
        assert isinstance(bundle, dict)
    except Exception:
        print_status("Jeton invalide. Recopie toute la ligne affichée par --export-session.", "error")
        return
    if bundle.get("site"):
        set_setting("films_site_url", str(bundle["site"]).rstrip("/"))
    for key in _SESSION_KEYS:
        if bundle.get(key) is not None:
            set_setting(key, bundle[key])
    print_status("Session importée ✅ (cookie, User-Agent, base d'API, profil).", "success")
    site = Site()
    ok, reason = site.probe_session()
    if ok:
        print_status("Vérifiée : l'API répond. Tu peux lancer les téléchargements (--no-browser conseillé).",
                     "success")
    else:
        print_status(f"⚠️ L'API ne répond pas encore ({reason}). Vérifie que le PC fixe et le serveur sont "
                     "sur le MÊME réseau (même IP publique), et réexporte un jeton frais si besoin.", "warning")


def deliver_session_token(token, out=None, push=None, push_dir="~/code/NBA-Downloader"):
    """Livre le jeton : écrit dans un fichier (--out), pousse sur le serveur en
    SSH (--push), ou l'affiche pour copier-coller."""
    if out:
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(token + "\n")
            print_status(f"Jeton écrit dans {out}.", "success")
        except OSError as e:
            print_status(f"Écriture impossible ({e}).", "error")
    if push:
        import subprocess
        # ~ doit rester non quoté pour s'étendre côté serveur ; un chemin absolu
        # est quoté pour gérer les espaces.
        cd = f"cd {push_dir}" if push_dir.startswith("~") else f'cd "{push_dir}"'
        remote = f"{cd} && ./start.sh films --import-session {token}"
        print_status(f"Import de la session sur {push} via SSH…", "loading")
        try:
            rc = subprocess.call(["ssh", push, remote])
        except FileNotFoundError:
            print_status("ssh introuvable sur cette machine.", "error")
            rc = 1
        if rc == 0:
            print_status(f"Session poussée sur {push} ✅", "success")
        else:
            print_status(f"Échec de l'import distant (code {rc}). Colle la commande à la main :", "warning")
            print(f"\n   ssh {push} \"cd {push_dir} && ./start.sh films --import-session {token}\"\n")
    if not out and not push:
        print_separator(title="JETON DE SESSION")
        print_status("Sur le serveur (SSH), colle cette commande :", "info")
        print(f"\n   python main.py films --import-session {token}\n")
    print_status("Le cookie expire au bout de quelques jours : réexporte un jeton quand l'API recommence "
                 "à refuser.", "info")


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
    parser.add_argument("--alldebrid-key", metavar="CLÉ",
                        help="Clé API AllDebrid pour débrider zone-telechargement (mémorisée, jamais commitée)")
    parser.add_argument("--zone-site", metavar="URL",
                        help="URL du site zone-telechargement si le domaine change (mémorisée)")
    parser.add_argument("--profile-id", help="Valeur de l'en-tête x-profile-id (mémorisée)")
    parser.add_argument("--cookie", help="En-tête Cookie de session, si l'API l'exige (mémorisé)")
    parser.add_argument("--user-agent", help="User-Agent du navigateur ayant obtenu le cookie (mémorisé)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Ne pas tenter le navigateur headless pour obtenir la session automatiquement")
    parser.add_argument("--browser-visible", action="store_true",
                        help="Navigateur visible (si le challenge Cloudflare ne passe pas en invisible ; nécessite un écran)")
    parser.add_argument("--export-session", action="store_true",
                        help="(machine AVEC navigateur) capture la session et imprime un jeton à importer sur le serveur SSH")
    parser.add_argument("--import-session", metavar="JETON",
                        help="(serveur SSH SANS navigateur) importe le jeton généré par --export-session")
    parser.add_argument("--push", metavar="SSH",
                        help="(avec --export-session) importe le jeton sur le serveur via ssh, ex. user@serveur")
    parser.add_argument("--push-dir", metavar="DIR", default="~/code/NBA-Downloader",
                        help="dossier du projet sur le serveur distant (défaut ~/code/NBA-Downloader)")
    parser.add_argument("--out", metavar="FICHIER",
                        help="(avec --export-session) écrit le jeton dans ce fichier au lieu de l'afficher")
    parser.add_argument("--profile-dir", metavar="DIR",
                        help="dossier de profil navigateur persistant (garde la session Cloudflare entre deux lancements)")
    parser.add_argument("--no-profile", action="store_true",
                        help="ne pas utiliser de profil persistant (session navigateur éphémère)")
    parser.add_argument("--refresh-session", action="store_true",
                        help="rafraîchit la session (navigateur si besoin) puis quitte — pour une tâche planifiée sur le serveur")
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
    if args.zone_site:
        set_setting("films_zone_site_url", args.zone_site.rstrip("/"))
    if args.alldebrid_key:
        set_setting("films_alldebrid_apikey", args.alldebrid_key.strip())
        from src.films.alldebrid import AllDebrid, AllDebridError
        try:
            who = AllDebrid().check()
            print_status(f"Clé AllDebrid enregistrée ✅ (compte : {who}).", "success")
        except AllDebridError as e:
            print_status(f"Clé AllDebrid enregistrée, mais la vérification a échoué : {e}", "warning")
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

    from src.films import session as _browser_session
    profile_dir = None if args.no_profile else (args.profile_dir or _browser_session.DEFAULT_PROFILE_DIR)

    # Transfert de session vers un serveur sans navigateur (SSH / headless).
    if args.import_session:
        import_session(args.import_session)
        return
    if args.export_session:
        token = export_session(args.browser_visible, profile_dir=profile_dir)
        if token:
            deliver_session_token(token, out=args.out, push=args.push, push_dir=args.push_dir)
        return

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

    # Rafraîchissement seul (tâche planifiée sur le serveur) : garde la session
    # valide puis quitte, sans rien télécharger.
    if args.refresh_session:
        ok = ensure_session(site, sys.stdin.isatty(), use_browser=not args.no_browser,
                            browser_visible=args.browser_visible, profile_dir=profile_dir)
        print_status("Session à jour ✅" if ok else "Échec du rafraîchissement de la session.",
                     "success" if ok else "error")
        return

    # Nakios (streaming) exige une session Cloudflare : on l'obtient si possible,
    # mais ce n'est plus bloquant — zone-telechargement (+ AllDebrid) peut suffire
    # à lui seul, et inversement.
    nakios_ready = ensure_session(site, sys.stdin.isatty(), use_browser=not args.no_browser,
                                  browser_visible=args.browser_visible, profile_dir=profile_dir)
    if not nakios_ready:
        print_status("Nakios (streaming) indisponible pour l'instant (session non obtenue).", "warning")

    providers = build_providers(site, nakios_ready)
    if not providers:
        print_status("Aucune source active : ni session Nakios, ni clé AllDebrid pour "
                     "zone-telechargement. Configure au moins l'une des deux "
                     "(--alldebrid-key \"VOTRE_CLÉ\").", "error")
        return
    print_status("Sources actives : " + ", ".join(p.label for p in providers) + ".", "info")
    print()

    try:
        # Mode direct (ligne de commande)
        media = None
        if args.url:
            media = media_from_url(args.url, providers)
            if media is None:
                print_status("URL non reconnue (nakios .../series|film/<id>, ou zone .../?p=film&id=…), "
                             "ou le fournisseur correspondant n'est pas actif.", "error")
                return
        elif args.search:
            results = search_all(providers, args.search)
            if not results:
                print_status("Aucun résultat.", "error")
                return
            print_results(results)
            pick = ask(f"Numéro (1-{len(results)}) : ")
            if not (pick.isdigit() and 1 <= int(pick) <= len(results)):
                return
            media = results[int(pick) - 1]

        if media is not None:
            process(media.provider, media, choose_dest(args.dest), args.season, args.episodes)
            return

        # Mode interactif
        while True:
            media = choose_media(providers)
            if media is None:
                break
            process(media.provider, media, choose_dest(args.dest), args.season, args.episodes)
            if not yes("\nTélécharger autre chose ?", default=False):
                break
            print()
    finally:
        if getattr(site, "browser", None) is not None:
            site.browser.close()
            site.browser = None


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
