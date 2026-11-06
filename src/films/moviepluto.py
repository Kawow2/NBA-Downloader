"""Fournisseur « MoviePluto » (https://moviepluto.cc) — films & séries en
**VOSTFR / VOST** (version originale sous-titrée).

moviepluto n'héberge aucune vidéo : c'est une façade (SPA React) qui, à partir
d'un identifiant **TMDB** (le `1399` de
`https://moviepluto.cc/watch/game-of-thrones/1399/1/1` = Game of Thrones),
ouvre l'un d'une quinzaine de **lecteurs tiers** (vidzee, cinemaos, vidup,
filmu…). Le site les présente sous des noms de fruits (APPLE, BLUEBERRY…).

Comme l'identifiant est un id TMDB, ce fournisseur **réutilise la recherche /
les détails / les saisons / épisodes de l'API « façon TMDB » déjà utilisée par
nakios** (src/films/site.Site) pour trouver le bon id, puis construit l'URL du
lecteur moviepluto pour ce média. Le téléchargement effectif passe ensuite par
le pipeline commun (`download_part` : yt-dlp, extracteur intégré, ffmpeg…).

Choix du lecteur
----------------
Tous les lecteurs ne proposent pas forcément du VOSTFR ni un flux récupérable.
Le lecteur à utiliser se fige dans les réglages (`films_moviepluto_server`,
par nom de fruit ou par domaine, ex. « BLUEBERRY » ou « vidzee ») : repérez sur
moviepluto le serveur qui joue bien en VOSTFR, et indiquez-le ici. Sans réglage,
tous les lecteurs connus sont proposés dans l'ordre par défaut (sans garantie de
langue).
"""
import re
from urllib.parse import urlparse

import requests

from src.var import DEFAULT_USER_AGENT, print_status
from src.utils.config.config import get_setting
from src.films.site import Media, Episode, Source, Site
from src.nba.site import host_display_name

DEFAULT_SITE_URL = "https://moviepluto.cc"
TMDB_API = "https://api.themoviedb.org/3"

# Nom de fruit (tel qu'affiché sur moviepluto) -> (base, gabarit film, gabarit
# série). Les gabarits emploient {base} (terminé par « / »), {id} (TMDB),
# {season}, {episode}. Extraits du site : la plupart suivent
# « base + movie/{id} » et « base + tv/{id}/{season}/{episode} », quelques-uns
# ont un préfixe propre (embed/…, player/…).
SERVERS = {
    "BLUEBERRY":  ("https://player.vidzee.wtf/",     "{base}embed/movie/{id}",  "{base}embed/tv/{id}/{season}/{episode}"),
    "APRICOT":    ("https://cinemaos.tech/",         "{base}player/{id}",       "{base}player/{id}/{season}/{episode}"),
    "AVOCADO":    ("https://vidup.to/",              "{base}movie/{id}",        "{base}tv/{id}/{season}/{episode}"),
    "BANANA":     ("https://embed.filmu.in/",        "{base}movie/{id}",        "{base}tv/{id}/{season}/{episode}"),
    "APPLE":      ("https://player.videasy.to/",     "{base}movie/{id}",        "{base}tv/{id}/{season}/{episode}"),
    "PLUM":       ("https://vidnest.pro/",           "{base}movie/{id}",        "{base}tv/{id}/{season}/{episode}"),
    "PINEBERRY":  ("https://vidlink.pro/",           "{base}movie/{id}",        "{base}tv/{id}/{season}/{episode}"),
    "CHERRY":     ("https://vidsrc.sh/",             "{base}embed/movie/{id}",  "{base}embed/tv/{id}/{season}/{episode}"),
    "LIME":       ("https://embed.vidrift.net/",     "{base}embed/movie/{id}",  "{base}embed/tv/{id}/{season}/{episode}"),
    "CANTALOUPE": ("https://vidfast.pro/",           "{base}movie/{id}",        "{base}tv/{id}/{season}/{episode}"),
    "TANGERINE":  ("https://player.vidlove.cc/embed/", "{base}movie/{id}",      "{base}tv/{id}/{season}/{episode}"),
    "PEAR":       ("https://ythd.org/",              "{base}embed/{id}",        "{base}embed/tv/{id}/{season}/{episode}"),
    "GRAPE":      ("https://vidly.pro/",             "{base}movie/{id}",        "{base}tv/{id}/{season}/{episode}"),
    "PASSION":    ("https://vidbolt.pro/",           "{base}movie/{id}",        "{base}tv/{id}/{season}/{episode}"),
}

# Ordre d'essai quand on demande explicitement « tous » les serveurs.
DEFAULT_ORDER = ["BLUEBERRY", "APRICOT", "AVOCADO", "BANANA", "APPLE", "PLUM",
                 "PINEBERRY", "CHERRY", "LIME", "CANTALOUPE", "TANGERINE",
                 "PEAR", "GRAPE", "PASSION"]

# Serveur utilisé par défaut (aucun réglage) : un seul lecteur = pas d'étape de
# choix. Vidnest (PLUM) renvoie un flux .m3u8/.mpd bien captable.
DEFAULT_SERVER = "vidnest"

# /watch/<slug>/<id>                      -> film
# /watch/<slug>/<id>/<season>/<episode>   -> série (saison/épisode optionnels)
_WATCH_RE = re.compile(r"/watch/[^/]+/(\d+)(?:/(\d+))?(?:/(\d+))?", re.I)


def parse_watch_url(url):
    """(type, id, saison, épisode) depuis une URL moviepluto, ex.
    'https://moviepluto.cc/watch/game-of-thrones/1399/1/1' ->
    ('tv', 1399, 1, 1), ou None. Sans saison/épisode -> ('movie', id, None, None)."""
    if not url:
        return None
    m = _WATCH_RE.search(url)
    if not m:
        return None
    tmdb = int(m.group(1))
    season = int(m.group(2)) if m.group(2) else None
    episode = int(m.group(3)) if m.group(3) else None
    return ("tv" if season is not None else "movie"), tmdb, season, episode


class MoviePluto:
    name = "moviepluto"
    label = "MoviePluto (VOSTFR)"
    debrid = None

    def __init__(self, site=None):
        # Métadonnées (recherche, saisons, épisodes), dans l'ordre :
        #   1. TMDB en direct si une clé est configurée (films_tmdb_key) —
        #      indépendant, recommandé ;
        #   2. sinon le client « façon TMDB » de nakios (src/films/site.Site),
        #      s'il est disponible ;
        #   3. sinon aucune recherche : on colle une URL moviepluto (l'id TMDB
        #      y est). moviepluto reste jouable sans métadonnées.
        self.site = site
        self.base = (get_setting("films_moviepluto_site_url") or DEFAULT_SITE_URL).rstrip("/")
        self.tmdb_key = (get_setting("films_tmdb_key") or "").strip() or None

    def metadata_source(self):
        if self.tmdb_key:
            return "tmdb"
        if self.site is not None:
            return "nakios"
        return "none"

    # ----------------------------------------------------------------- TMDB
    def _tmdb(self, path, params=None):
        p = {"api_key": self.tmdb_key, "language": "fr-FR"}
        if params:
            p.update(params)
        try:
            r = requests.get(TMDB_API + path, params=p, timeout=20,
                             headers={"User-Agent": DEFAULT_USER_AGENT})
        except requests.RequestException as e:
            print_status(f"TMDB injoignable : {str(e)[:100]}", "error")
            return None
        if r.status_code == 401:
            print_status("Clé TMDB refusée (401). Vérifie films_tmdb_key / --tmdb-key.", "error")
            return None
        if r.status_code >= 400:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    # ---------------------------------------------------- recherche / métadonnées
    def search(self, query, kind=None):
        # L'id renvoyé est un id TMDB, celui qu'attendent les lecteurs moviepluto.
        if self.tmdb_key:
            data = self._tmdb("/search/multi", {"query": query, "page": 1})
            results = (data or {}).get("results") or []
            out = []
            limit = 40 if kind else 20
            for item in results:
                if not isinstance(item, dict) or item.get("media_type") not in ("movie", "tv"):
                    continue
                if not item.get("id"):
                    continue
                m = Media(item)
                if kind and m.media_type != kind:
                    continue
                m.provider = self
                out.append(m)
                if len(out) >= limit:
                    break
            return out
        if self.site is None:
            print_status("Recherche moviepluto indisponible (ni clé TMDB, ni session nakios). "
                         "Colle une URL moviepluto, ou configure une clé TMDB (--tmdb-key).", "warning")
            return []
        res = self.site.search(query, limit=40 if kind else 20)
        if kind:
            res = [m for m in res if m.media_type == kind]
        for m in res:
            m.provider = self
        return res

    def details(self, media):
        if self.tmdb_key:
            path = f"/tv/{media.id}" if media.is_series else f"/movie/{media.id}"
            data = self._tmdb(path)
            if data:
                merged = Media(data, media_type=media.media_type)
                if merged.title in ("?", None):
                    merged.title = media.title
                merged.year = merged.year or media.year
                for attr in ("pinned_season", "pinned_episode"):
                    if hasattr(media, attr):
                        setattr(merged, attr, getattr(media, attr))
                seasons = Site._seasons_from(data) if merged.is_series else []
                return merged, seasons
            # TMDB n'a pas répondu : on garde ce qu'on a (titre du slug…).
            return media, []
        if self.site is None:
            return media, []
        return self.site.details(media)

    def episodes(self, media, season):
        if self.tmdb_key:
            data = self._tmdb(f"/tv/{media.id}/season/{season}")
            raw = (data or {}).get("episodes") or []
            eps = []
            for e in raw:
                num = e.get("episode_number")
                if num is None:
                    continue
                eps.append(Episode(season, int(num), e.get("name") or e.get("title")))
            eps.sort(key=lambda e: e.number)
            return eps
        if self.site is None:
            return []
        return self.site.episodes(media, season)

    # ---------------------------------------------------------------- lecteurs
    def _match_server(self, pref):
        """Nom de fruit correspondant à un réglage (fruit ou domaine), ou None."""
        p = (pref or "").strip().lower()
        if not p:
            return None
        for name, (base, _, _) in SERVERS.items():
            host = (urlparse(base).hostname or "").lower()
            if p == name.lower() or p == host or (p in base.lower()) or (p in host):
                return name
        return None

    def _servers_to_try(self):
        pref = (get_setting("films_moviepluto_server") or "").strip()
        # « all » / « tous » : on essaie tous les serveurs dans l'ordre.
        if pref.lower() in ("all", "tous", "tout", "*"):
            return [(name,) + SERVERS[name] for name in DEFAULT_ORDER if name in SERVERS]
        # Aucun réglage : Vidnest par défaut (un seul lecteur, pas d'étape).
        if not pref:
            pref = DEFAULT_SERVER
        name = self._match_server(pref)
        if name:
            return [(name,) + SERVERS[name]]
        print_status(f"Serveur moviepluto « {pref} » inconnu : j'essaie tous les serveurs.", "warning")
        return [(name,) + SERVERS[name] for name in DEFAULT_ORDER if name in SERVERS]

    def sources(self, media, season=None, episode=None):
        """URLs des lecteurs moviepluto pour ce film / cet épisode, en Source
        étiquetées VOSTFR. `download_part` tentera ensuite l'extraction."""
        out = []
        for name, base, movie_t, tv_t in self._servers_to_try():
            try:
                if media.is_series:
                    if season is None or episode is None:
                        continue
                    url = tv_t.format(base=base, id=media.id, season=int(season), episode=int(episode))
                else:
                    url = movie_t.format(base=base, id=media.id)
            except Exception:
                continue
            src = Source(url, label=name, lang="VOSTFR")
            src.host = f"{name} · {src.host}"
            src.provider = self
            src.page_referer = self.base + "/"
            # L'URL est une page de lecteur JS (pas un flux direct) : il faut la
            # résoudre dans un navigateur pour en capter le .m3u8 (voir
            # src/films/moviepluto_resolve.py).
            src.needs_resolve = True
            out.append(src)
        return out
