"""Extraction des lecteurs moviepluto par **capture réseau** (Playwright).

Les lecteurs de moviepluto (vidzee, cinemaos, vidup, vidnest, filmu…) sont des
players JavaScript obfusqués : ils récupèrent le flux `.m3u8` via leur propre
API interne, que ni yt-dlp ni un extracteur statique ne savent reproduire. La
méthode fiable, qui marche pour la plupart sans rétro-ingénierie lecteur par
lecteur : **ouvrir le lecteur dans un vrai navigateur, le laisser jouer, et
« sniffer » l'URL `.m3u8` (et les pistes de sous-titres `.vtt`/`.srt`) qui
passent sur le réseau**. On télécharge ensuite ce flux avec le pipeline habituel.

Réutilise Playwright (déjà requis pour la session nakios). Variable d'env
facultative FILMS_CHROMIUM_PATH = binaire Chrome/Chromium à utiliser.
"""
import os
import re
import time
from urllib.parse import urlparse

DEFAULT_REFERER = "https://moviepluto.cc/"

# Flux vidéo et pistes de sous-titres repérés dans le trafic réseau.
_STREAM_RE = re.compile(r"\.(m3u8|mpd)(\?|$)", re.I)
_MP4_RE = re.compile(r"\.mp4(\?|$)", re.I)
_SUB_RE = re.compile(r"\.(vtt|srt)(\?|$)", re.I)

# Bruit à ignorer (pubs, trackers, miniatures…).
_NOISE = ("google", "doubleclick", "facebook", "analytics", "sentry", "cloudflareinsights",
          "adservice", "/ads", "popads", "histats", "yandex", "/thumb", "sprite", "preview",
          "storage.googleapis.com")


def available():
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def install_hint():
    return "pip install playwright && playwright install chromium"


def _is_noise(url):
    low = url.lower()
    return any(n in low for n in _NOISE)


def _guess_lang(url):
    """Code langue déduit d'une URL de sous-titre (…/french.vtt, ?lang=fr…)."""
    low = url.lower()
    for code, words in (("fr", ("vostfr", "french", "francais", "français", "/fre", "fr.vtt", "fr.srt", "lang=fr", "_fr", "-fr")),
                        ("en", ("english", "anglais", "/eng", "en.vtt", "en.srt", "lang=en", "_en", "-en"))):
        if any(w in low for w in words):
            return code
    return None


class MoviePlutoResolver:
    """Garde un navigateur ouvert et résout plusieurs lecteurs à la suite."""

    def __init__(self, status=None, headless=True):
        self._status = status
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None

    def _say(self, msg, kind="info"):
        if self._status:
            self._status(msg, kind)

    def start(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            self._say("Playwright indisponible : " + install_hint(), "error")
            return False
        try:
            self._pw = sync_playwright().start()
            args = ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--mute-audio"]
            launch = {"headless": self.headless, "args": args}
            exe = os.environ.get("FILMS_CHROMIUM_PATH")
            attempts = [dict(launch, executable_path=exe)] if exe else \
                       [dict(launch, channel="chrome"), dict(launch)]
            last = None
            for kw in attempts:
                try:
                    self._browser = self._pw.chromium.launch(**kw)
                    break
                except Exception as e:
                    last = e
            if not self._browser:
                self._say(f"Lancement du navigateur impossible : {str(last)[:120]}", "error")
                return False
            self._context = self._browser.new_context(
                locale="fr-FR", viewport={"width": 1280, "height": 800},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"))
            return True
        except Exception as e:
            self._say(f"Navigateur : {str(e)[:150]}", "error")
            return False

    def resolve(self, embed_url, referer=None, timeout=45):
        """Ouvre `embed_url` et renvoie {'stream', 'referer', 'subtitles'} dès
        qu'un flux est capté, ou None. `referer` par défaut = moviepluto (les
        lecteurs le vérifient souvent)."""
        if not self._context:
            return None
        referer = referer or DEFAULT_REFERER
        streams, mp4s, subs = [], [], []

        def on_response(resp):
            try:
                url = resp.url
                if _is_noise(url):
                    return
                if _STREAM_RE.search(url):
                    streams.append((url, dict(resp.request.headers)))
                elif _MP4_RE.search(url):
                    mp4s.append((url, dict(resp.request.headers)))
                elif _SUB_RE.search(url):
                    subs.append(url)
            except Exception:
                pass

        page = self._context.new_page()
        page.on("response", on_response)
        # Les lecteurs ouvrent des pubs dans de nouveaux onglets : on ferme ces
        # pop-ups ENFANTS uniquement (surtout pas la page courante).
        page.on("popup", _safe_close_popup)
        # Certains lecteurs bloquent les requêtes "media" si on coupe trop ;
        # on laisse tout charger, on observe seulement.
        nav_ok = True
        try:
            page.goto(embed_url, referer=referer, wait_until="domcontentloaded",
                      timeout=min(timeout, 30) * 1000)
        except Exception:
            nav_ok = False  # domaine mort/bloqué : on n'attend pas 45 s pour rien

        # Si la page n'a pas chargé (DNS bloqué, hôte mort…), on abrège.
        deadline = time.time() + (timeout if nav_ok else 8)
        clicked = False
        while time.time() < deadline and not streams:
            if page.is_closed():
                break
            try:
                page.wait_for_timeout(1000)
            except Exception:
                break  # page/contexte fermé (pub, navigation…) : on s'arrête là
            # Beaucoup de players démarrent sur un clic : on tente une fois.
            if not clicked and time.time() > deadline - timeout + 3:
                for action in (lambda: page.mouse.click(640, 400),
                               lambda: page.keyboard.press("Space")):
                    try:
                        action()
                    except Exception:
                        pass
                clicked = True

        found = streams[0] if streams else (mp4s[0] if mp4s else None)
        result = None
        if found:
            stream_url, headers = found
            ref = headers.get("referer") or headers.get("Referer") or referer
            seen, sub_list = set(), []
            for u in subs:
                if u in seen:
                    continue
                seen.add(u)
                sub_list.append({"url": u, "lang": _guess_lang(u), "label": u})
            result = {"stream": stream_url, "referer": ref, "subtitles": sub_list}
        try:
            page.close()
        except Exception:
            pass
        return result

    def close(self):
        for obj, meth in ((self._context, "close"), (self._browser, "close"), (self._pw, "stop")):
            try:
                if obj:
                    getattr(obj, meth)()
            except Exception:
                pass
        self._context = self._browser = self._pw = None


def _safe_close_popup(page):
    try:
        page.close()
    except Exception:
        pass
