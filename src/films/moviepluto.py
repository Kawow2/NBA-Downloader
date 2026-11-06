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

from src.var import print_status
from src.utils.config.config import get_setting
from src.films.site import Media, Source
from src.nba.site import host_display_name

DEFAULT_SITE_URL = "https://moviepluto.cc"

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

# Ordre d'essai quand aucun serveur n'est figé dans les réglages.
DEFAULT_ORDER = ["BLUEBERRY", "APRICOT", "AVOCADO", "BANANA", "APPLE", "PLUM",
                 "PINEBERRY", "CHERRY", "LIME", "CANTALOUPE", "TANGERINE",
                 "PEAR", "GRAPE", "PASSION"]

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

    def __init__(self, site):
        # `site` est le client « façon TMDB » (src/films/site.Site) déjà
        # configuré : on s'en sert pour la recherche et les métadonnées.
        self.site = site
        self.base = (get_setting("films_moviepluto_site_url") or DEFAULT_SITE_URL).rstrip("/")

    # ---------------------------------------------------- recherche / métadonnées
    def search(self, query, kind=None):
        # Même API que nakios (TMDB) : on récupère l'id TMDB, qui est aussi
        # celui qu'attendent les lecteurs de moviepluto.
        res = self.site.search(query, limit=40 if kind else 20)
        if kind:
            res = [m for m in res if m.media_type == kind]
        for m in res:
            m.provider = self
        return res

    def details(self, media):
        return self.site.details(media)

    def episodes(self, media, season):
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
        if pref:
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
            out.append(src)
        return out
