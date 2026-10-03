"""Récupération AUTOMATIQUE de la session du site (cookie Cloudflare
`cf_clearance` + `nk_verified`) via un navigateur **headless**, pour ne pas
avoir à coller le cookie à la main.

Pourquoi un navigateur : le site est derrière Cloudflare et son API n'ouvre
qu'avec une session. `cf_clearance` est délivré après exécution du challenge
JavaScript de Cloudflare et est lié à l'IP + au User-Agent ; aucun client
HTTP « nu » ne peut le fabriquer. Un vrai Chromium, lui, passe le challenge
tout seul, puis on récupère ses cookies + son User-Agent pour les donner au
client HTTP habituel (curl_cffi) — qui, avec la bonne empreinte TLS, le bon
UA et la même IP, est accepté par Cloudflare.

Playwright est **optionnel** : s'il est absent, l'app retombe sur la saisie
manuelle du cookie. Installation :

    pip install playwright && playwright install chromium
"""

# Déclenche la pose de la session (cf_clearance + nk_verified) côté serveur,
# exactement comme l'app du site, et valide d'un coup que l'API répond du JSON.
_PROBE_JS = """async (base) => {
  try {
    const r = await fetch(base + '/api/search/multi?query=a&page=1',
                          {credentials: 'include', headers: {'accept': '*/*'}});
    const ct = (r.headers.get('content-type') || '').toLowerCase();
    const body = (await r.text()).slice(0, 80).trim().toLowerCase();
    const json = ct.includes('json') || body.startsWith('{') || body.startsWith('[');
    return {status: r.status, json: json};
  } catch (e) {
    return {status: 0, json: false, error: String(e)};
  }
}"""


def available():
    """True si Playwright est importable (le navigateur peut être tenté)."""
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def install_hint():
    return "pip install playwright && playwright install chromium"


def install(status=None):
    """Installe Playwright + le navigateur Chromium dans l'interpréteur
    courant (le .venv quand lancé via start.sh/ps1). True si disponible
    ensuite. À ne faire qu'une fois."""
    import subprocess
    import sys

    def say(msg, kind="info"):
        if status:
            status(msg, kind)

    say("Installation de Playwright (une seule fois, peut prendre ~1 min)…", "loading")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    except Exception as e:
        say(f"Installation échouée : {str(e)[:150]}. Faites-le à la main : {install_hint()}", "error")
        return False
    import importlib
    importlib.invalidate_caches()
    if available():
        say("Playwright installé ✅", "success")
        return True
    say("Playwright toujours indisponible après installation.", "error")
    return False


def grab_session(base_url, headless=True, timeout=60, status=None):
    """Ouvre base_url dans Chromium, laisse Cloudflare passer, déclenche un
    appel API pour poser la session, puis renvoie (cookie_header, user_agent)
    si l'API répond enfin du JSON — sinon None.

    `status` : fonction d'affichage optionnelle (print_status) ; sinon muet.
    """
    def say(msg, kind="info"):
        if status:
            status(msg, kind)

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        say("Playwright n'est pas installé : " + install_hint(), "warning")
        return None

    base = base_url.rstrip("/")
    say(f"Navigateur {'invisible' if headless else 'visible'} : obtention de la session sur {base}…", "loading")
    try:
        with sync_playwright() as p:
            try:
                # --disable-blink-features=AutomationControlled : réduit la
                # détection « navigateur piloté » par Cloudflare.
                browser = p.chromium.launch(
                    headless=headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception as e:
                # Chromium pas installé pour Playwright.
                say(f"Chromium introuvable pour Playwright ({str(e)[:80]}). "
                    f"Lancez : playwright install chromium", "error")
                return None
            context = browser.new_context(
                locale="fr-FR",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            try:
                page.goto(base + "/", wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception as e:
                say(f"Chargement impossible : {str(e)[:100]}", "error")
                browser.close()
                return None

            user_agent = page.evaluate("() => navigator.userAgent")

            # Cloudflare pose cf_clearance en quelques secondes après l'exécution
            # du challenge JS ; on sonde l'API en boucle (dans la page, donc
            # cookies + CF gérés par le navigateur) jusqu'à obtenir du JSON.
            import time
            deadline = time.time() + timeout
            got = False
            while time.time() < deadline:
                try:
                    res = page.evaluate(_PROBE_JS, base)
                except Exception:
                    res = {"status": 0, "json": False}
                if res.get("json") and res.get("status") == 200:
                    got = True
                    break
                page.wait_for_timeout(1500)

            cookies = context.cookies()
            browser.close()

        if not got:
            say("Le navigateur n'a pas obtenu de session (challenge Cloudflare non résolu en headless ?).",
                "warning")
            if headless:
                say("Essai possible en mode visible (--browser-visible) si vous avez un écran.", "info")
            return None

        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))
        if not cookie_header:
            return None
        say("Session obtenue automatiquement ✅", "success")
        return cookie_header, user_agent
    except Exception as e:
        say(f"Navigateur : {str(e)[:150]}", "error")
        return None
