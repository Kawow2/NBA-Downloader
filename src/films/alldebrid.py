"""Client minimal de l'API AllDebrid (débrideur).

Sert à transformer un lien protégé / d'hébergeur (dl-protect, 1fichier,
Uptobox, Rapidgator…) en **lien direct** téléchargeable à pleine vitesse,
pour les sources de type « téléchargement » (zone-telechargement).

Flux : un lien dl-protect est d'abord « déplié » (endpoint redirector) en
liens d'hébergeurs, puis chaque lien est « débloqué » (endpoint unlock) en
lien direct. La clé API est lue du réglage films_alldebrid_apikey (stockée
en local, jamais commitée).

API v4 : https://docs.alldebrid.com/
"""
import requests

from src.utils.config.config import get_setting

AGENT = "NBA-Downloader"
BASE = "https://api.alldebrid.com/v4"


class AllDebridError(Exception):
    pass


class AllDebrid:
    def __init__(self, apikey=None, session=None):
        self.apikey = apikey or get_setting("films_alldebrid_apikey") or ""
        self.session = session or requests.Session()

    @property
    def configured(self):
        return bool(self.apikey)

    def _get(self, path, **params):
        if not self.apikey:
            raise AllDebridError("clé API AllDebrid absente (réglage films_alldebrid_apikey / --alldebrid-key)")
        params.update(agent=AGENT, apikey=self.apikey)
        try:
            resp = self.session.get(f"{BASE}{path}", params=params, timeout=30)
        except requests.RequestException as e:
            raise AllDebridError(f"connexion AllDebrid impossible : {str(e)[:120]}")
        try:
            data = resp.json()
        except ValueError:
            raise AllDebridError(f"réponse AllDebrid non-JSON (HTTP {resp.status_code})")
        if data.get("status") != "success":
            err = data.get("error") or {}
            raise AllDebridError(err.get("message") or err.get("code") or "erreur AllDebrid inconnue")
        return data.get("data") or {}

    def check(self):
        """Renvoie le pseudo du compte si la clé est valide (sinon lève)."""
        return (self._get("/user").get("user") or {}).get("username")

    def redirector(self, link):
        """Déplie un lien protecteur/redirecteur (dl-protect…) en liens
        d'hébergeurs. Liste vide si non applicable."""
        try:
            return self._get("/link/redirector", link=link).get("links") or []
        except AllDebridError:
            return []

    def unlock(self, link):
        """(lien direct, nom de fichier, taille) pour un lien d'hébergeur."""
        d = self._get("/link/unlock", link=link)
        return d.get("link"), d.get("filename"), d.get("filesize")

    def resolve(self, link):
        """Lien dl-protect / hébergeur -> (lien direct, nom, taille). Déplie
        d'abord un éventuel protecteur, puis débloque le premier lien qui
        marche. Lève AllDebridError si rien ne se débloque."""
        candidates = self.redirector(link) or [link]
        errors = []
        for c in candidates:
            try:
                direct, filename, size = self.unlock(c)
                if direct:
                    return direct, filename, size
            except AllDebridError as e:
                errors.append(str(e))
        raise AllDebridError("; ".join(dict.fromkeys(errors)) or "aucun lien débloqué")
