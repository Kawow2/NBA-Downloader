"""Fournisseur « zone-telechargement » : site de **téléchargement direct**
(liens 1fichier/Uptobox/Rapidgator… protégés par dl-protect), pas de
streaming. Les qualités sont souvent bien meilleures que le streaming
(BluRay, 4K, REMUX).

Une page `?p=film&id=<id>-<slug>` = **une version** (une qualité/langue) d'un
film, avec ses liens dl-protect et une section « Qualités également
disponibles » listant les autres versions (qualité + langue + lien). On
récupère donc toutes les qualités en une seule page.

Le téléchargement effectif passe par un débrideur (AllDebrid) : voir
src/films/alldebrid.py. Films seulement pour l'instant (pas les séries).
"""
import re
import base64
from urllib.parse import quote_plus, urljoin, urlparse, parse_qs

import requests

from src.var import DEFAULT_USER_AGENT, print_status
from src.utils.config.config import get_setting
from src.films.site import Media, Source, _quality_from, _lang_from

DEFAULT_SITE_URL = "https://www.zone-telechargement.skin"

# Hauteur approximative pour classer les versions quand le libellé n'a pas de
# résolution explicite (DVDRIP, BDRIP…), meilleure qualité en premier.
_SOURCE_HEIGHT = [
    (re.compile(r"\b(4k|2160|uhd|remux)\b", re.I), 2160),
    (re.compile(r"\b(1080|fhd|blu-?ray|bdrip|brrip|hdlight)\b", re.I), 1080),
    (re.compile(r"\b(720|hd|hdrip|web-?dl|webrip|hdtv)\b", re.I), 720),
    (re.compile(r"\b(480|sd|dvdrip|dvd)\b", re.I), 480),
]


def _height(label):
    res = _quality_from(label)[1]
    if res:
        return res
    for rx, h in _SOURCE_HEIGHT:
        if rx.search(label or ""):
            return h
    return 0


def _slug_title(slug):
    return re.sub(r"[-_]+", " ", re.sub(r"^\d+-", "", slug or "")).strip().title()


class Zone:
    name = "zone"
    label = "Zone-Téléchargement"

    def __init__(self, debrid=None, session=None):
        self.base = (get_setting("films_zone_site_url") or DEFAULT_SITE_URL).rstrip("/")
        self.debrid = debrid
        self.session = session or self._make_session()

    def _make_session(self):
        try:
            from curl_cffi import requests as creq
            s = creq.Session(impersonate="chrome")
        except Exception:
            s = requests.Session()
            s.headers["User-Agent"] = DEFAULT_USER_AGENT
        return s

    def _get(self, url):
        full = url if url.startswith("http") else self.base + url
        try:
            resp = self.session.get(full, timeout=25)
        except Exception as e:
            print_status(f"zone-telechargement inaccessible : {str(e)[:100]}", "error")
            return None
        if resp.status_code >= 400:
            return None
        return resp.text

    # ----------------------------------------------------------------- search
    def search(self, query, limit=20):
        html = self._get(f"/?p=films&search={quote_plus(query)}")
        if not html:
            return []
        groups = {}  # slug sans id -> (post_id, slug)
        order = []
        for m in re.finditer(r'[?&]p=film&id=(\d+)-([a-z0-9\-]+)', html, re.I):
            post_id, slug = m.group(1), m.group(2).lower()
            if slug not in groups:
                groups[slug] = (post_id, slug)
                order.append(slug)
        out = []
        for slug in order[:limit]:
            post_id, slug = groups[slug]
            media = Media({"id": post_id, "title": _slug_title(slug)}, media_type="movie")
            media.provider = self
            media.url = f"/?p=film&id={post_id}-{slug}"
            out.append(media)
        return out

    # ---------------------------------------------------------------- details
    def details(self, media):
        """Complète le média (titre) ; pas de saisons (films)."""
        return media, []

    # ----------------------------------------------------------------- sources
    def sources(self, media, season=None, episode=None):
        """Toutes les versions/qualités du film, en Source (needs_debrid)."""
        url = getattr(media, "url", None) or f"/?p=film&id={media.id}"
        html = self._get(url)
        if not html:
            return []
        out, seen = [], set()

        # Version de la page courante : qualité/langue décodées du fn= d'un
        # lien dl-protect (ex. "Interstellar [Blu-Ray 1080p] - TRUEFRENCH").
        cur_label, cur_lang = self._current_version(html)
        if cur_label:
            out.append(self._source(cur_label, cur_lang, url))
            seen.add(url.split("id=")[-1])

        # Autres qualités : liens vers les autres posts, libellés "QUALITÉ (LANGUE)".
        section = html.split("également disponibles", 1)
        area = section[1] if len(section) > 1 else html
        for m in re.finditer(r'<a[^>]+href="([^"]*\?p=film&id=[^"]+)"[^>]*>(.*?)</a>', area, re.I | re.S):
            href = m.group(1).replace("&amp;", "&")
            text = re.sub(r"<[^>]+>", " ", m.group(2))
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                continue
            key = href.split("id=")[-1]
            if key in seen:
                continue
            seen.add(key)
            lang = _lang_from(text)
            qual = re.sub(r"\s*\(.*\)\s*$", "", text).strip() or text  # retire "(MULTI (FRENCH))"
            out.append(self._source(qual, lang, href))

        out.sort(key=lambda s: s.height, reverse=True)
        return out

    def _source(self, quality_label, lang, page_url):
        src = Source(self.base + page_url if page_url.startswith("/") else page_url,
                     label=quality_label, quality=quality_label, lang=lang)
        src.host = self.label
        src.quality = quality_label or src.quality
        src.height = _height(quality_label)
        src.needs_debrid = True
        src.provider = self
        return src

    @staticmethod
    def _current_version(html):
        """(qualité, langue) de la version affichée, depuis le fn= base64 d'un
        lien dl-protect (fn=base64("Titre [Qualité] - LANGUE"))."""
        for fn in re.findall(r'dl-protect\.link/(?!rqts-url)[^"?]*\?fn=([A-Za-z0-9+/=]+)', html):
            try:
                dec = base64.b64decode(fn + "=" * (-len(fn) % 4)).decode("utf-8", "ignore")
            except Exception:
                continue
            m = re.search(r"\[([^\]]+)\]", dec)
            if m:
                lang = _lang_from(dec)
                return m.group(1).strip(), lang
        return None, None

    # --------------------------------------------------------- liens dl-protect
    def download_links(self, page_url):
        """Liens dl-protect (par hébergeur) d'une page de version, à passer au
        débrideur. Les liens 'rqts-url' (agrégateur) servent de repli."""
        html = self._get(page_url)
        if not html:
            return []
        per_host, aggregate = [], []
        for m in re.finditer(r'href="(https://dl-protect\.link/[^"]+)"', html, re.I):
            link = m.group(1).replace("&amp;", "&")
            if "rqts-url" in link:
                aggregate.append(link)
            elif link not in per_host:
                per_host.append(link)
        # Dédup en gardant l'ordre.
        seen, ordered = set(), []
        for link in per_host + aggregate:
            if link not in seen:
                seen.add(link)
                ordered.append(link)
        return ordered
