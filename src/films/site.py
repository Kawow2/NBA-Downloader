"""Client de l'API d'un site de streaming « façon TMDB » (nakios.rent par
défaut) : recherche, détails d'un film / d'une série, liste des saisons et
des épisodes, et enfin les lecteurs/flux vidéo d'un film ou d'un épisode.

Le site est une application web (SPA) qui parle à une API JSON. L'endpoint
de recherche fourni par le site — `/api/search/multi?query=&page=` — est
calqué sur l'API TMDB ; on suppose donc que le reste de l'API l'est aussi
(`/api/movie/{id}`, `/api/tv/{id}`, `/api/tv/{id}/season/{n}`), ce qui est le
cas sur la grande majorité de ces sites. Les endpoints « détails / saisons /
épisodes » sont essayés dans l'ordre jusqu'à ce que l'un réponde, donc un
préfixe différent (`/api/serie/...`, etc.) est rattrapé automatiquement.

La récupération des **lecteurs vidéo** (la seule partie vraiment propre au
site, hors TMDB) est faite par `sources()` : on essaie plusieurs endpoints
candidats, puis on parcourt le JSON pour en extraire toute URL qui ressemble
à un hébergeur connu (voe, uqload, filemoon, vidmoly, sibnet, ok.ru…) ou à un
flux direct (.m3u8/.mp4). Le bon endpoint peut être figé une fois pour toutes
via le réglage `films_sources_path` (voir plus bas).
"""
import json
import os
import re
from urllib.parse import urljoin, urlparse

import requests

from src.var import Colors, DEFAULT_USER_AGENT, SourceDomains, print_status
from src.utils.config.config import get_setting, set_setting
# Réutilise la détection d'hébergeurs vidéo éprouvée du module NBA.
from src.nba.site import _KNOWN_VIDEO_HOSTS, _EMBED_PATH, host_display_name

DEFAULT_SITE_URL = "https://nakios.rent"

_STREAM_RE = re.compile(r"\.(m3u8|mp4|mkv|mpd)(\?|$)", re.IGNORECASE)

# Endpoints candidats, essayés dans l'ordre. {id} {season} {episode} sont
# remplacés. Le premier qui répond un JSON exploitable gagne.
_MOVIE_DETAILS_PATHS = ("/api/movie/{id}", "/api/film/{id}", "/api/details/movie/{id}", "/api/media/movie/{id}")
_TV_DETAILS_PATHS = ("/api/tv/{id}", "/api/serie/{id}", "/api/series/{id}", "/api/details/tv/{id}", "/api/media/tv/{id}")
_SEASON_PATHS = ("/api/tv/{id}/season/{season}", "/api/serie/{id}/season/{season}",
                 "/api/series/{id}/season/{season}", "/api/tv/{id}/{season}")

# Endpoints candidats pour les LECTEURS d'un film / d'un épisode (partie
# propre au site, hors TMDB). Réglage `films_sources_path` pour forcer le bon.
_MOVIE_SOURCE_PATHS = (
    "/api/movie/{id}/sources", "/api/movie/{id}/stream", "/api/movie/{id}/watch",
    "/api/movie/{id}/links", "/api/movie/{id}/players", "/api/sources/movie/{id}",
    "/api/watch/movie/{id}", "/api/stream/movie/{id}", "/api/film/{id}/sources",
)
_EPISODE_SOURCE_PATHS = (
    "/api/tv/{id}/season/{season}/episode/{episode}/sources",
    "/api/tv/{id}/season/{season}/episode/{episode}/stream",
    "/api/tv/{id}/season/{season}/episode/{episode}/watch",
    "/api/tv/{id}/season/{season}/episode/{episode}/links",
    "/api/tv/{id}/{season}/{episode}/sources",
    "/api/sources/tv/{id}/{season}/{episode}",
    "/api/watch/tv/{id}/{season}/{episode}",
    "/api/stream/tv/{id}/{season}/{episode}",
)


def _year_of(date_str):
    m = re.search(r"(19|20)\d{2}", str(date_str or ""))
    return m.group(0) if m else None


class Media:
    """Un film ou une série renvoyé par la recherche / les détails."""

    def __init__(self, data, media_type=None):
        self.raw = data or {}
        self.id = self.raw.get("id") or self.raw.get("tmdb_id")
        mt = media_type or self.raw.get("media_type")
        if not mt:
            # Les détails ne renvoient pas media_type : une série a des
            # saisons / une date de 1re diffusion, un film un titre + date de
            # sortie.
            mt = "tv" if (self.raw.get("seasons") or self.raw.get("first_air_date")
                          or (self.raw.get("name") and not self.raw.get("title"))) else "movie"
        self.media_type = mt
        self.title = (self.raw.get("title") or self.raw.get("name")
                      or self.raw.get("original_title") or self.raw.get("original_name") or "?")
        self.year = _year_of(self.raw.get("release_date") or self.raw.get("first_air_date"))
        self.overview = self.raw.get("overview") or ""

    @property
    def is_series(self):
        return self.media_type == "tv"

    def __repr__(self):
        return f"Media({self.media_type}, {self.id}, {self.title!r}, {self.year})"


class Season:
    def __init__(self, number, episode_count=None, name=None):
        self.number = number
        self.episode_count = episode_count
        self.name = name


class Episode:
    def __init__(self, season, number, title=None):
        self.season = season
        self.number = number
        self.title = title


class Source:
    """Un lecteur vidéo : une URL (iframe d'hébergeur ou flux direct)."""

    def __init__(self, url, label=None, quality=None, lang=None):
        self.url = url
        self.host = host_display_name(url)
        self.quality = quality
        self.lang = lang
        self.label = label or self.host

    def __repr__(self):
        return f"Source({self.host}, {self.url})"


class Site:
    def __init__(self, base_url=None, debug=False):
        self.base = (base_url or get_setting("films_site_url") or DEFAULT_SITE_URL).rstrip("/")
        self.host = urlparse(self.base).hostname or ""
        self.debug = debug
        self.last_status = None
        self.last_error = None
        self.impersonating = False
        self.session = self._make_session()

    # ------------------------------------------------------------------ HTTP
    def _headers(self):
        # x-profile-id et le cookie de session reproduisent le « credentials:
        # include » du site (profil « à la Netflix »). Vides par défaut : la
        # recherche marche souvent sans, mais l'API peut exiger une session —
        # collez alors le cookie via le réglage films_cookie / --cookie.
        headers = {
            "User-Agent": get_setting("films_user_agent") or DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": self.base + "/",
            "Origin": self.base,
            "X-Profile-Id": str(get_setting("films_profile_id") or ""),
        }
        cookie = get_setting("films_cookie")
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _make_session(self):
        # curl_cffi imite l'empreinte TLS de Chrome : indispensable quand le
        # site est derrière Cloudflare, qui bloque le client « requests » sur
        # son empreinte TLS (403 « Just a moment ») même avec les bons
        # en-têtes. Repli sur requests si curl_cffi est absent.
        try:
            from curl_cffi import requests as creq
            session = creq.Session(impersonate="chrome")
            self.impersonating = True
        except Exception:
            session = requests.Session()
        try:
            session.headers.update(self._headers())
        except Exception:
            pass
        return session

    def _get_json(self, path, params=None, referer=None):
        url = path if path.startswith("http") else self.base + path
        extra = {"Referer": referer} if referer else None
        self.last_error = None
        try:
            resp = self.session.get(url, params=params, timeout=25, headers=extra)
        except Exception as e:
            self.last_status = None
            self.last_error = f"connexion impossible : {str(e)[:150]}"
            return None
        self.last_status = resp.status_code
        self._dump(url, resp)
        text = resp.text or ""
        if resp.status_code >= 400 or _looks_cloudflare(text):
            data = self._retry_cloudscraper(url, params, extra)
            if data is not None:
                return data
            if _looks_cloudflare(text) or resp.status_code in (403, 503):
                self.last_error = f"bloqué par Cloudflare / session requise (HTTP {resp.status_code})"
            else:
                self.last_error = f"l'API a répondu HTTP {resp.status_code}"
            return None
        try:
            return resp.json()
        except ValueError:
            low = text[:300].lstrip().lower()
            if low.startswith("<!doctype") or low.startswith("<html") or "<html" in low[:50]:
                # Le serveur renvoie l'appli (index.html) au lieu du JSON :
                # typiquement une requête API sans session (le site utilise
                # « credentials: include »). Il faut le cookie de session.
                self.last_error = ("l'API a renvoyé la page du site (HTML), pas du JSON — cookie de "
                                   "session requis (le site utilise « credentials: include »)")
            else:
                snippet = text[:200].replace("\n", " ").strip()
                self.last_error = (f"réponse non-JSON (HTTP {resp.status_code}) : {snippet!r}"
                                   if snippet else "réponse vide")
            return None

    def _retry_cloudscraper(self, url, params, extra):
        """Dernier recours contre Cloudflare quand curl_cffi n'a pas suffi."""
        try:
            import cloudscraper
        except Exception:
            return None
        try:
            scraper = cloudscraper.create_scraper()
            headers = self._headers()
            if extra:
                headers.update(extra)
            resp = scraper.get(url, params=params, headers=headers, timeout=30)
        except Exception:
            return None
        self.last_status = resp.status_code
        self._dump(url, resp)
        if resp.status_code >= 400:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def _dump(self, url, resp):
        if not self.debug:
            return
        os.makedirs("debug", exist_ok=True)
        name = re.sub(r"[^a-zA-Z0-9]+", "_", urlparse(url).path).strip("_") or "index"
        path = os.path.join("debug", f"{name[:120]}_{resp.status_code}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(resp.text)
            print_status(f"[debug] réponse enregistrée : {path}", "info")
        except OSError:
            pass

    def _first_json(self, paths, **fmt):
        """Premier endpoint candidat qui répond un JSON non vide."""
        for template in paths:
            data = self._get_json(template.format(**fmt))
            if data:
                if self.debug:
                    print_status(f"[debug] OK : {template.format(**fmt)}", "info")
                return data, template
        return None, None

    # ----------------------------------------------------------------- search
    def search(self, query, limit=20):
        data = self._get_json("/api/search/multi", params={"query": query, "page": 1})
        if data is None and self.last_error:
            print_status(f"Recherche impossible : {self.last_error}", "error")
            if self.last_status in (401, 403) or "session" in (self.last_error or "").lower():
                print_status("Le site exige probablement une session : collez le cookie "
                             "(Réglages, ou --cookie \"...\") depuis F12 → Réseau → une requête /api/... "
                             "→ en-tête « cookie ».", "info")
        results = []
        if isinstance(data, dict):
            results = data.get("results") or data.get("data") or []
        elif isinstance(data, list):
            results = data
        out = []
        for item in results:
            if not isinstance(item, dict):
                continue
            mt = item.get("media_type")
            if mt == "person":
                continue
            if not item.get("id"):
                continue
            # La recherche « multi » peut contenir films et séries ; on garde
            # les deux, on ignore le reste.
            if mt not in ("movie", "tv", None):
                continue
            out.append(Media(item))
            if len(out) >= limit:
                break
        return out

    # ---------------------------------------------------------------- details
    def details(self, media):
        """Complète un Media (titre, année) et, pour une série, renvoie ses
        saisons. Renvoie (media, [Season])."""
        paths = _TV_DETAILS_PATHS if media.is_series else _MOVIE_DETAILS_PATHS
        data, _ = self._first_json(paths, id=media.id)
        if data:
            merged = Media(data, media_type=media.media_type)
            # Garde un titre déjà connu si les détails sont plus pauvres.
            if merged.title in ("?", None):
                merged.title = media.title
            merged.year = merged.year or media.year
            media = merged
        seasons = []
        if media.is_series:
            seasons = self._seasons_from(data or media.raw)
        return media, seasons

    @staticmethod
    def _seasons_from(data):
        seasons = []
        raw_seasons = (data or {}).get("seasons") or []
        for s in raw_seasons:
            if not isinstance(s, dict):
                continue
            num = s.get("season_number")
            if num is None:
                continue
            seasons.append(Season(int(num), s.get("episode_count"), s.get("name")))
        seasons.sort(key=lambda s: s.number)
        return seasons

    def episodes(self, series, season_number):
        """Liste des épisodes d'une saison."""
        data, _ = self._first_json(_SEASON_PATHS, id=series.id, season=season_number)
        raw = []
        if isinstance(data, dict):
            raw = data.get("episodes") or data.get("results") or []
        elif isinstance(data, list):
            raw = data
        episodes = []
        for e in raw:
            if not isinstance(e, dict):
                continue
            num = e.get("episode_number") or e.get("number")
            if num is None:
                continue
            episodes.append(Episode(season_number, int(num), e.get("name") or e.get("title")))
        episodes.sort(key=lambda e: e.number)
        # Repli : si l'API ne détaille pas les épisodes mais que la saison
        # annonce un nombre, on génère 1..N (noms inconnus).
        if not episodes:
            count = None
            if isinstance(data, dict):
                count = data.get("episode_count")
            if not count:
                for s in self._seasons_from(series.raw):
                    if s.number == season_number:
                        count = s.episode_count
                        break
            if count:
                episodes = [Episode(season_number, n) for n in range(1, int(count) + 1)]
        return episodes

    # ----------------------------------------------------------------- sources
    def sources(self, media, season=None, episode=None):
        """Lecteurs/flux d'un film ou d'un épisode, dans l'ordre renvoyé par
        le site. Essaie l'endpoint figé (réglage films_sources_path) puis les
        candidats, et parcourt le JSON pour en extraire les URLs."""
        override = get_setting("films_sources_path")
        templates = []
        if override:
            templates.append(override)
        if media.is_series:
            templates += list(_EPISODE_SOURCE_PATHS)
            fmt = {"id": media.id, "season": season, "episode": episode}
        else:
            templates += list(_MOVIE_SOURCE_PATHS)
            fmt = {"id": media.id, "season": season or 0, "episode": episode or 0}

        for template in templates:
            try:
                path = template.format(**fmt)
            except (KeyError, IndexError):
                continue
            data = self._get_json(path)
            if not data:
                continue
            found = self._parse_sources(data)
            if found:
                if self.debug:
                    print_status(f"[debug] {len(found)} lecteur(s) via {path}", "info")
                return found

        # Dernier recours : certains sites mettent les lecteurs directement
        # dans les détails du film / de l'épisode.
        detail_paths = _TV_DETAILS_PATHS if media.is_series else _MOVIE_DETAILS_PATHS
        data, _ = self._first_json(detail_paths, id=media.id)
        if data:
            found = self._parse_sources(data)
            if found:
                return found
        return []

    def _parse_sources(self, data):
        seen, out = set(), []
        for url in _iter_urls(data):
            url = _clean_url(url)
            if not url or url.rstrip("/") in seen:
                continue
            if self._is_own_host(url):
                continue  # liens internes (affiches, pages du site)
            if not _looks_like_stream(url):
                continue
            seen.add(url.rstrip("/"))
            out.append(Source(url))
        return out

    def _is_own_host(self, url):
        host = (urlparse(url).hostname or "").lower()
        own = self.host.lower()
        return host == own or host.endswith("." + own.lstrip("w.")) or host.endswith(own)


# ---------------------------------------------------------------- helpers
def _looks_cloudflare(text):
    low = (text or "")[:3000].lower()
    return ("just a moment" in low or "cf-chl" in low or "challenge-platform" in low
            or "attention required" in low or "enable javascript and cookies" in low)


def _iter_urls(obj):
    """Toutes les chaînes http(s) d'un JSON imbriqué."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_urls(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_urls(v)
    elif isinstance(obj, str):
        s = obj.strip()
        if s.startswith("http") or s.startswith("//"):
            yield s


def _clean_url(url):
    if url.startswith("//"):
        url = "https:" + url
    url = url.replace("\\/", "/").strip().strip('"\'')
    return url if url.startswith("http") else None


def _looks_like_stream(url):
    """Vrai si l'URL est un flux direct (.m3u8/.mp4…) ou un hébergeur vidéo
    connu / une page d'embed."""
    low = url.lower()
    if re.search(r"\.(jpe?g|png|gif|webp|svg|css|js|ico|woff2?)(\?|$)", low):
        return False
    host = (urlparse(url).hostname or "").lower()
    if _STREAM_RE.search(low):
        return True
    if SourceDomains.is_valid_url(url):
        return True
    if any(k in host for k in _KNOWN_VIDEO_HOSTS):
        return True
    return bool(_EMBED_PATH.search(urlparse(url).path))


def parse_media_url(url):
    """(type, id) depuis une URL/chemin nakios.rent, ex.
    'https://nakios.rent/series/87108' -> ('tv', 87108), ou None."""
    if not url:
        return None
    m = re.search(r"/(series|serie|tv|show|shows)/(\d+)", url)
    if m:
        return "tv", int(m.group(2))
    m = re.search(r"/(film|films|movie|movies)/(\d+)", url)
    if m:
        return "movie", int(m.group(2))
    return None
