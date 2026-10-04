"""Session automatique du site (Cloudflare `cf_clearance` + `nk_verified`)
via un **vrai navigateur** piloté par Playwright — pour ne jamais coller de
cookie à la main.

Pourquoi : l'API n'ouvre qu'avec une session, et `cf_clearance` n'est délivré
qu'après le challenge JavaScript de Cloudflare (lié à l'IP + au User-Agent).
Aucun client HTTP ne peut le fabriquer ; un navigateur, si.

`BrowserSession` fait deux choses :
  1. ouvre le site, franchit Cloudflare (anti-détection ; repli en navigateur
     visible sous écran virtuel Xvfb pour les serveurs sans affichage) ;
  2. sert ensuite d'**API** : `get_json()` exécute le `fetch()` dans la page
     (donc cookies + Cloudflare gérés par le navigateur). On peut aussi en
     extraire les cookies + User-Agent pour les réutiliser avec le client
     rapide (curl_cffi) quand Cloudflare l'accepte.

Playwright est optionnel :  pip install playwright && playwright install chromium
Variable d'env facultative : FILMS_CHROMIUM_PATH = chemin d'un binaire Chrome/
Chromium à utiliser (sinon celui de Playwright).
"""
import json
import os
import sys
import time
from urllib.parse import urlencode

_PROBE_PATH = "/api/search/multi?query=a&page=1"

# Réduit la détection « navigateur piloté » (navigator.webdriver, etc.).
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['fr-FR','fr','en-US','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || { runtime: {} };
"""

# fetch() exécuté DANS la page : cookies + Cloudflare gérés par le navigateur.
# `headers` = en-têtes capturés sur un vrai appel de l'appli (x-profile-id…).
_FETCH_JS = """async ({url, headers}) => {
  try {
    const h = Object.assign({'accept': '*/*'}, headers || {});
    const r = await fetch(url, {credentials: 'include', headers: h});
    const ct = (r.headers.get('content-type') || '').toLowerCase();
    const body = await r.text();
    return {status: r.status, ct: ct, body: body};
  } catch (e) { return {status: 0, ct: '', body: '', error: String(e)}; }
}"""


def available():
    """True si Playwright est importable."""
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def install_hint():
    return "pip install playwright && playwright install chromium"


def install(status=None):
    """Installe Playwright + Chromium dans l'interpréteur courant (le .venv
    quand lancé via start.sh/ps1). True si disponible ensuite."""
    import subprocess

    def say(msg, kind="info"):
        if status:
            status(msg, kind)

    say("Installation de Playwright (une seule fois, peut prendre ~1 min)…", "loading")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    except Exception as e:
        say(f"Installation échouée : {str(e)[:150]}. À faire à la main : {install_hint()}", "error")
        return False
    import importlib
    importlib.invalidate_caches()
    if available():
        say("Playwright installé ✅", "success")
        return True
    say("Playwright toujours indisponible après installation.", "error")
    return False


def _looks_json(ct, body):
    head = (body or "").lstrip()[:1]
    return "json" in (ct or "") or head in ("{", "[")


# Profil navigateur persistant : une fois Cloudflare franchi, la clearance est
# gardée dans ce profil, si bien que les captures suivantes (même headless, en
# tâche planifiée) passent sans intervention. Hors du dépôt.
DEFAULT_PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".nakios-downloader", "chromium-profile")


class BrowserSession:
    """Navigateur qui franchit Cloudflare puis sert d'API. Avec un profil
    persistant (profile_dir), la session Cloudflare est conservée entre deux
    lancements."""

    def __init__(self, base, status=None, profile_dir=None):
        self.base = base.rstrip("/")
        self._status = status
        self.profile_dir = profile_dir  # None = session éphémère
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._display = None
        self.user_agent = None
        self._api_ok = False
        self.api_headers = {}   # en-têtes capturés sur un vrai appel API de l'appli
        self.api_base = None    # origine réelle de l'API détectée (scheme://host)
        self.api_urls = []      # échantillon d'URLs d'API réellement appelées

    def _say(self, msg, kind="info"):
        if self._status:
            self._status(msg, kind)

    # ------------------------------------------------------------- ouverture
    def open(self, headless=True, allow_xvfb=True, timeout=None):
        """Ouvre le site et franchit Cloudflare. True quand l'API répond du
        JSON. Repli automatique en navigateur visible sous Xvfb sur un serveur
        Linux sans affichage."""
        mode = "visible" if not headless else "invisible"
        self._say(f"Navigateur {mode} : obtention de la session sur {self.base}…", "loading")
        t = timeout or (60 if headless else 180)
        if self._try_open(headless=headless, timeout=t):
            return True
        self.close()
        if headless and allow_xvfb and sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
            if self._start_xvfb():
                self._say("Nouvel essai en navigateur visible dans l'écran virtuel…", "loading")
                if self._try_open(headless=False, timeout=180):
                    return True
                self.close()
        return False

    def _on_response(self, response):
        """Observe les appels API réussis de l'appli : en déduit la vraie base
        de l'API (scheme://host) et capture les en-têtes utiles (x-profile-id,
        authorization…) pour les rejouer. Garde en priorité un appel de
        recherche (le plus représentatif)."""
        try:
            url = response.url
            if "/api/" not in url or response.status != 200:
                return
            if "json" not in (response.headers.get("content-type") or "").lower():
                return
            from urllib.parse import urlparse
            o = urlparse(url)
            self.api_base = f"{o.scheme}://{o.netloc}"
            bare = url.split("?")[0]
            if bare not in [u.split("?")[0] for u in self.api_urls]:
                self.api_urls = (self.api_urls + [url])[:20]
            req = response.request.headers
            keep = {k: v for k, v in req.items()
                    if k.lower() == "authorization"
                    or (k.lower().startswith("x-") and k.lower() != "x-requested-with")}
            if "search" in o.path or not self.api_headers:
                self.api_headers = keep
            self._api_ok = True
        except Exception:
            pass

    def observed_summary(self):
        """Résumé lisible des appels API réellement vus (pour diagnostic)."""
        lines = []
        if self.api_base:
            lines.append(f"API détectée sur : {self.api_base}")
        for u in self.api_urls[:12]:
            lines.append("   " + u)
        if self.api_headers:
            lines.append("En-têtes ajoutés par l'appli : " + ", ".join(sorted(self.api_headers)))
        return "\n".join(lines)

    def _try_open(self, headless, timeout):
        try:
            if not self._make_context(headless):
                return False
            self._context.add_init_script(_STEALTH_JS)
            self._page = self._context.new_page()
            self._api_ok = False
            # On détecte la session en observant les vrais appels API de la
            # page (plus fiable qu'un fetch synthétique : l'appli ajoute ses
            # propres en-têtes, ex. x-profile-id).
            self._page.on("response", self._on_response)
            self._page.goto(self.base + "/", wait_until="domcontentloaded", timeout=timeout * 1000)
            self.user_agent = self._page.evaluate("() => navigator.userAgent")
            if not headless:
                self._say("➡️  Dans la fenêtre : passe la vérification Cloudflare et attends que le site "
                          "s'affiche ; si rien ne vient, lance une recherche sur le site. Je détecte la "
                          "session et je continue tout seul (ne ferme pas la fenêtre).", "info")
            deadline = time.time() + timeout
            warned = False
            while time.time() < deadline:
                if self._api_ok or self._probe():
                    self._say("Session obtenue par le navigateur ✅", "success")
                    return True
                self._page.wait_for_timeout(1500)
                if not headless and not warned and time.time() > deadline - timeout + 50:
                    self._say("Toujours en attente — vérifie que le site est affiché, et fais une recherche "
                              "dessus pour forcer la détection.", "warning")
                    warned = True
            self._say("Session non détectée dans le temps imparti.", "warning")
            return False
        except Exception as e:
            self._say(f"Navigateur : {str(e)[:150]}", "error")
            return False

    def _make_context(self, headless):
        """Crée self._context (persistant si profile_dir, sinon éphémère)."""
        from playwright.sync_api import sync_playwright
        if self._pw is None:
            self._pw = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        ctx_opts = {"locale": "fr-FR", "timezone_id": "Europe/Paris",
                    "viewport": {"width": 1280, "height": 800}}
        launch = {"headless": headless, "args": args}
        exe = os.environ.get("FILMS_CHROMIUM_PATH")
        if exe:
            launch["executable_path"] = exe
        # Profil persistant : un seul contexte, pas de navigateur séparé. C'est
        # lui qui garde la clearance Cloudflare d'un lancement à l'autre.
        if self.profile_dir:
            try:
                os.makedirs(self.profile_dir, exist_ok=True)
                self._context = self._pw.chromium.launch_persistent_context(
                    self.profile_dir, **launch, **ctx_opts)
                self._browser = None
                return True
            except Exception as e:
                self._say(f"Profil persistant indisponible ({str(e)[:80]}), session éphémère.", "warning")
        # Éphémère : vrai Chrome d'abord (meilleur passage Cloudflare), puis le
        # Chromium embarqué de Playwright.
        attempts = [dict(launch)] if exe else [dict(launch, channel="chrome"), dict(launch)]
        last = None
        for kw in attempts:
            try:
                self._browser = self._pw.chromium.launch(**kw)
                self._context = self._browser.new_context(**ctx_opts)
                return True
            except Exception as e:
                last = e
        self._say(f"Lancement du navigateur impossible : {str(last)[:120]}", "error")
        return False

    def _start_xvfb(self):
        try:
            from pyvirtualdisplay import Display
        except Exception:
            self._say("Serveur sans écran : pour un navigateur visible, installez un écran virtuel : "
                      "sudo apt install xvfb && pip install pyvirtualdisplay", "warning")
            return False
        try:
            self._display = Display(visible=0, size=(1280, 800))
            self._display.start()
            return True
        except Exception as e:
            self._say(f"Xvfb indisponible ({str(e)[:80]}) : sudo apt install xvfb", "warning")
            return False

    # ------------------------------------------------------------------ API
    def _url(self, path):
        if path.startswith("http"):
            return path
        base = self.api_base or self.base
        return base + path if path.startswith("/") else base + "/" + path

    def _fetch(self, path):
        try:
            return self._page.evaluate(
                _FETCH_JS, {"url": self._url(path), "headers": self.api_headers or {}})
        except Exception:
            return {"status": 0, "ct": "", "body": ""}

    def _probe(self):
        res = self._fetch(_PROBE_PATH)
        return res.get("status") == 200 and _looks_json(res.get("ct", ""), res.get("body", ""))

    def get_json(self, path, params=None):
        """Exécute GET base+path dans la page (avec les en-têtes capturés) et
        renvoie le JSON, ou None."""
        full = path
        if params:
            full = path + ("&" if "?" in path else "?") + urlencode(params)
        res = self._fetch(full)
        if res.get("status") == 200 and _looks_json(res.get("ct", ""), res.get("body", "")):
            try:
                return json.loads(res["body"])
            except Exception:
                return None
        return None

    def cookies_and_ua(self):
        try:
            cookies = self._context.cookies()
        except Exception:
            cookies = []
        header = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))
        return header, self.user_agent

    def close(self):
        for obj, meth in ((self._context, "close"), (self._browser, "close"), (self._pw, "stop")):
            try:
                if obj:
                    getattr(obj, meth)()
            except Exception:
                pass
        self._context = self._browser = self._pw = None
        if self._display:
            try:
                self._display.stop()
            except Exception:
                pass
            self._display = None


def grab_session(base_url, headless=True, timeout=90, status=None, profile_dir=DEFAULT_PROFILE_DIR):
    """Compat : ouvre un BrowserSession, renvoie (cookie_header, user_agent)
    puis ferme. Préférer BrowserSession pour garder le navigateur ouvert."""
    bs = BrowserSession(base_url, status=status, profile_dir=profile_dir)
    try:
        if not bs.open(headless=headless, timeout=timeout):
            return None
        cookie, ua = bs.cookies_and_ua()
        return (cookie, ua) if cookie else None
    finally:
        bs.close()
