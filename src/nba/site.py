"""basketball-video.com scraping: latest games, search, and the list of
servers / parts embedded on a game page.

The site's markup isn't hard-coded anywhere here: game links, search forms
and video embeds are detected generically (iframes, data-* attributes,
tab panes, "Part N" labels...) so a theme change on the site doesn't break
everything at once. Run main.py with --debug to see what was detected.
"""
import base64
import os
import re
import unicodedata
from datetime import date
from urllib.parse import urljoin, urlparse, parse_qsl, quote_plus

import requests
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from src.var import Colors, DEFAULT_USER_AGENT, SourceDomains, print_status
from src.utils.config.config import get_setting, set_setting

DEFAULT_SITE_URL = "https://basketball-video.com"

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Links in these regions are site chrome (menus, sidebars, footers), never
# the game list / the players of the current page.
_CHROME_SELECTOR = "header, nav, footer, aside, .sidebar, #sidebar, .widget, .menu, .navigation, .breadcrumbs, .comments, #comments"

_NOT_A_GAME_PATH = re.compile(
    r"/(category|categories|tag|tags|page|author|search|login|register|user|feed|wp-|static|"
    r"contact|dmca|privacy|about|terms|faq|rss|xfsearch|lastnews|newposts|favorites)|"
    r"index\.php|\?|\.(jpe?g|png|gif|webp|css|js|xml|txt)$",
    re.IGNORECASE,
)
_GAME_HINT = re.compile(r"(-vs?-|-at-|replay|full-game|game-\d|nba|finals|playoffs)", re.IGNORECASE)

# Hosts that are never a video player (social buttons, CDNs, trackers...).
_BLOCKED_HOSTS = (
    "facebook.", "fb.com", "twitter.", "x.com", "t.me", "telegram.", "whatsapp.", "reddit.",
    "pinterest.", "instagram.", "tiktok.", "discord.", "disqus.", "gravatar.", "wordpress.",
    "google.", "googleapis.", "gstatic.", "googletagmanager.", "doubleclick.", "cloudflare.",
    "jsdelivr.", "jquery.", "bootstrapcdn.", "fontawesome.", "addtoany.", "sharethis.",
    "linkedin.", "tumblr.", "paypal.", "patreon.", "amazon.", "yandex.ru/metrika", "mc.yandex.",
    "histats.", "statcounter.", "schema.org", "w3.org", "gmpg.org", "ogp.me",
    "highperformanceformat.", "googlesyndication.", "adsterra", "profitablecpm", "ucoz.net",
)

# Path shapes used by embeddable video hosts (voe /e/xxx, ok.ru
# /videoembed/xxx, streamtape /e/, filemoon /e/, mixdrop /e/, dood /e/...).
_EMBED_PATH = re.compile(r"(/e/|/embed|embed-|/v/|/d/|/f/|/videoembed/|/video/|/video_ext|/player|/watch|\.m3u8|\.mp4)", re.IGNORECASE)

_KNOWN_VIDEO_HOSTS = tuple(SourceDomains.PLAYERS) + (
    "ok.ru", "odnoklassniki", "dailymotion", "dai.ly", "streamtape", "mixdrop", "dood", "d000d",
    "ds2play", "vk.com", "vkvideo", "vidhide", "streamwish", "wishembed", "mp4upload",
    "upstream", "vidoza", "streamlare", "fembed", "mega.nz", "drive.google", "youtube",
    "youtu.be", "rumble", "streamable", "vimeo", "voe", "filemoon", "lulu", "bigwarp",
    "savefiles", "vidguard", "listeamed", "vembed", "bembed", "vtube", "streamvid",
    "videzz", "vidoza", "turbovid", "hglink", "hgcloud", "embedrise", "rutube", "pixeldrain",
)

_PART_RE = re.compile(
    r"\b(?:part(?:ie)?|pt\.?)\s*#?\s*(\d{1,2})\b"
    r"|\b(\d)\s*(?:st|nd|rd|th)?\s*(?:half|quarter|qtr|mi-temps)\b"
    r"|\b(first|second|third|fourth)\s+(?:half|quarter)\b"
    r"|\bq\s*([1-4])\b"
    r"|\b(ot|overtime|prolongation)\b",
    re.IGNORECASE,
)
_WORD_NUM = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_SERVER_RE = re.compile(r"\b(server|serveur|mirror|source|player|lecteur|link|lien|host)\s*#?\s*(\d{1,2}|[a-z]\b)(\s*\([^)]{1,20}\))?", re.IGNORECASE)
# Buttons leading to an intermediate page that holds the actual player
# (basketball-video.com: "Server #1 (OK)" + a "Watch" button to a blog page).
_WATCH_TEXT = re.compile(r"\b(watch|play|stream|server|mirror|link|part|voir|regarder)\b", re.IGNORECASE)
_LABEL_HINT = re.compile(
    r"\b(part(?:ie)?|pt\.?\s*\d|half|quarter|qtr|q[1-4]|server|serveur|mirror|source|player|lecteur|link|lien|full game|overtime|ot)\b",
    re.IGNORECASE,
)


def _is_blocked(url):
    """Domain-aware match against _BLOCKED_HOSTS ("x.com" must not match
    "guideanimaux.com"): "name." matches a domain label, "a.b" a domain or
    its subdomains, "a.b/path" a URL prefix, anything else a host substring."""
    host = (urlparse(url).hostname or "").lower()
    low = url.lower()
    for b in _BLOCKED_HOSTS:
        if "/" in b:
            if b in low:
                return True
        elif b.endswith("."):
            if host.startswith(b) or ("." + b) in host:
                return True
        elif "." in b:
            if host == b or host.endswith("." + b):
                return True
        elif b in host:
            return True
    return False


def normalize_text(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def parse_date(text):
    """Find a game date in a title or URL slug ('June 13, 2026',
    'june--13-2026', '13.06.2026', '2026-06-13'). Returns a date or None."""
    if not text:
        return None
    t = text.lower()
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s,\-_./]*(\d{1,2})(?:st|nd|rd|th)?(?:\s*-\s*\d{1,2})?[\s,\-_./]+(\d{4})\b", t)
    if m:
        month, day, year = MONTHS[m.group(1)], int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-_./]*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s,\-_./]*(\d{4})\b", t)
        if m:
            day, month, year = int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))
        else:
            m = re.search(r"\b(20\d{2})[\-_./](\d{1,2})[\-_./](\d{1,2})\b", t)
            if m:
                year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                m = re.search(r"\b(\d{1,2})[\-_./](\d{1,2})[\-_./](20\d{2})\b", t)
                if not m:
                    return None
                a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
                # US sites write MM.DD.YYYY; a first number > 12 can only be a day.
                month, day = (b, a) if a > 12 else (a, b)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _humanize_slug(url):
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"\.html?$", "", slug)
    slug = re.sub(r"^\d+-", "", slug)
    return re.sub(r"[-_]+", " ", slug).strip().title()


def _clean_title(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    # "<title> | Basketball Video" / "<title> - basketball-video.com"
    text = re.split(r"\s+[|–—»]\s+", text)[0]
    text = re.sub(r"\s+-\s+basketball[\s-]*video(\.com)?$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _strip_www(host):
    return host[4:] if host.startswith("www.") else host


def _in_chrome(tag, strict=True):
    """True if tag sits in site chrome. strict (game lists) also skips
    'related'/'popular' blocks; the lenient mode (player detection) only
    skips sidebars, footers and comments so player tab bars survive."""
    pattern = (r"\b(sidebar|widget|menu|navigation|breadcrumbs?|comments?|related|popular|site-header|site-footer|footer)\b|\baside"
               if strict else r"\b(sidebar|widget|comments?|site-footer|footer)\b")
    for parent in tag.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in (("header", "nav", "footer", "aside") if strict else ("footer", "aside")):
            return True
        classes = " ".join(parent.get("class") or []).lower()
        pid = (parent.get("id") or "").lower()
        if re.search(pattern, classes + " " + pid):
            return True
    return False


class Game:
    def __init__(self, url, title):
        self.url = url
        self.title = _clean_title(title) or _humanize_slug(url)
        self.date = parse_date(self.title) or parse_date(url)

    def __repr__(self):
        return f"Game({self.title!r}, {self.date}, {self.url})"


class Part:
    def __init__(self, url, label, number=None, referer=None):
        self.url = url
        self.label = label
        self.number = number
        self.referer = referer  # page embedding the player, if not the game page

    def __repr__(self):
        return f"Part({self.number}, {self.label!r}, {self.url})"


class Server:
    def __init__(self, name, host, section=None):
        self.name = name
        self.host = host
        self.section = section  # the game, when a page holds several games
        self.parts = []

    def __repr__(self):
        return f"Server({self.name!r}, {self.parts})"


def host_display_name(url):
    host = (urlparse(url).hostname or "").lower()
    for domain, name in SourceDomains.DISPLAY_NAMES.items():
        if domain.lower() in host:
            return name
    if "ok.ru" in host or "odnoklassniki" in host:
        return "OK.ru"
    if "dailymotion" in host or "dai.ly" in host:
        return "Dailymotion"
    parts = [p for p in host.split(".") if p not in ("www", "m", "embed", "player", "video", "cdn")]
    if len(parts) >= 2:
        return parts[-2].capitalize()
    return host or "?"


def _part_number(label):
    if not label:
        return None
    m = _PART_RE.search(label)
    if not m:
        return None
    if m.group(1):
        return int(m.group(1))
    if m.group(2):
        return int(m.group(2))
    if m.group(3):
        return _WORD_NUM[m.group(3).lower()]
    if m.group(4):
        return int(m.group(4))
    return 99  # overtime: after every regular part


def _server_label(label):
    if not label:
        return None
    m = _SERVER_RE.search(label)
    if m:
        paren = re.sub(r"\s+", " ", m.group(3) or "").rstrip()
        return f"{m.group(1).capitalize()} {m.group(2).upper()}{paren}"
    return None


class Site:
    def __init__(self, base_url=None, debug=False):
        self.base = (base_url or get_setting("nba_site_url") or DEFAULT_SITE_URL).rstrip("/")
        self.host = urlparse(self.base).hostname or ""
        self.debug = debug
        self.session = requests.Session()
        self._setup_session()
        self._cf_prompted = False

    # ------------------------------------------------------------------ HTTP
    def _setup_session(self):
        ua = get_setting("nba_user_agent") or DEFAULT_USER_AGENT
        self.session.headers.update({
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
            "Referer": self.base + "/",
        })
        cf = get_setting("nba_cf_clearance")
        if cf:
            self.session.cookies.set("cf_clearance", cf, domain="." + _strip_www(self.host))

    @staticmethod
    def _is_cloudflare_challenge(resp):
        if resp.status_code not in (403, 429, 503):
            return False
        body = resp.text[:5000].lower()
        return "cf-chl" in body or "just a moment" in body or "challenge-platform" in body or "cf-ray" in str(resp.headers).lower()

    def _ask_cloudflare_cookie(self):
        print_status(f"{self.host} est protégé par Cloudflare.", "warning")
        print_status(f"1. Ouvrez {self.base} dans votre navigateur et passez la vérification.", "info")
        print_status("2. F12 → Application → Cookies → copiez la valeur de 'cf_clearance'.", "info")
        cf = input(f"{Colors.BOLD}cf_clearance : {Colors.ENDC}").strip()
        print_status("3. F12 → Console → tapez navigator.userAgent et copiez le résultat (sans les guillemets).", "info")
        ua = input(f"{Colors.BOLD}User-Agent : {Colors.ENDC}").strip().strip("'\"")
        if cf:
            set_setting("nba_cf_clearance", cf)
        if ua:
            set_setting("nba_user_agent", ua)
        self._setup_session()

    def get(self, url, method="GET", data=None, allow_error=False, referer=None):
        headers = {"Referer": referer} if referer else None
        self.last_status = None
        for attempt in range(2):
            try:
                if method == "POST":
                    resp = self.session.post(url, data=data, timeout=20, headers=headers)
                else:
                    resp = self.session.get(url, timeout=20, headers=headers)
            except requests.RequestException as e:
                self.last_status = f"erreur réseau : {str(e)[:120]}"
                if allow_error:
                    return None
                print_status(f"Connexion impossible à {url} : {e}", "error")
                return None
            self.last_status = resp.status_code
            if self._is_cloudflare_challenge(resp) and attempt == 0 and not self._cf_prompted and self._is_own_host(url):
                self._cf_prompted = True
                self._ask_cloudflare_cookie()
                continue
            if resp.status_code >= 400:
                self._dump(url, resp.text, suffix=f"_HTTP{resp.status_code}")
                if not allow_error:
                    print_status(f"{url} a répondu {resp.status_code}", "error")
                return None
            self._dump(url, resp.text)
            return resp.text
        return None

    def _dump(self, url, html, suffix=""):
        if not self.debug:
            return
        os.makedirs("debug", exist_ok=True)
        name = re.sub(r"[^a-zA-Z0-9]+", "_", urlparse(url).path + "_" + urlparse(url).query).strip("_") or "index"
        path = os.path.join("debug", name[:120] + suffix + ".html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print_status(f"[debug] page enregistrée : {path}", "info")

    def _is_own_host(self, url):
        host = _strip_www((urlparse(url).hostname or "").lower())
        own = _strip_www(self.host.lower())
        return host == own or host.endswith("." + own)

    # --------------------------------------------------------- game listings
    def extract_games(self, html, page_url):
        soup = BeautifulSoup(html, "html.parser")
        # uCoz (basketball-video.com) lists entries in #allEntries; anything
        # outside it is sidebars / "popular" blocks.
        root = soup.select_one("#allEntries") or soup
        found = {}
        order = []
        for a in root.find_all("a", href=True):
            href = urljoin(page_url, a["href"].strip()).split("#")[0]
            if not href.startswith("http") or not self._is_own_host(href):
                continue
            path = urlparse(href).path
            if len(path.strip("/")) < 12 or _NOT_A_GAME_PATH.search(href):
                continue
            slug = re.sub(r"\.html?$", "", path.rstrip("/").split("/")[-1])
            if slug.count("-") < 3:
                continue
            if _in_chrome(a):
                continue
            title = (a.get("title") or "").strip() or a.get_text(" ", strip=True)
            if not title:
                img = a.find("img")
                title = (img.get("alt") or img.get("title") or "") if img else ""
            key = href.rstrip("/")
            if key not in found:
                found[key] = [href, title]
                order.append(key)
            elif len(title) > len(found[key][1]):
                found[key][1] = title
        games = [Game(found[k][0], found[k][1]) for k in order]
        strong = [g for g in games if _GAME_HINT.search(urlparse(g.url).path)]
        return strong or games

    @staticmethod
    def _sort_newest_first(games):
        dated = [g for g in games if g.date]
        undated = [g for g in games if not g.date]
        dated.sort(key=lambda g: g.date, reverse=True)
        return dated + undated

    def _page_url(self, n):
        return self.base + "/" if n == 1 else f"{self.base}/page/{n}/"

    def latest_games(self, limit=10, max_pages=3):
        games, seen = [], set()
        for n in range(1, max_pages + 1):
            html = self.get(self._page_url(n), allow_error=(n > 1))
            if not html:
                break
            for g in self.extract_games(html, self._page_url(n)):
                if g.url.rstrip("/") not in seen:
                    seen.add(g.url.rstrip("/"))
                    games.append(g)
            if len(games) >= limit:
                break
        return self._sort_newest_first(games)[:limit]

    # ----------------------------------------------------------------- search
    def _search_requests(self, query):
        """Candidate search requests, the site's own search form first."""
        reqs = []
        home = self.get(self.base + "/", allow_error=True)
        if home:
            soup = BeautifulSoup(home, "html.parser")
            for form in soup.find_all("form"):
                text_inputs = [i for i in form.find_all("input") if (i.get("type") or "text").lower() in ("text", "search") and i.get("name")]
                if not text_inputs:
                    continue
                action = urljoin(self.base + "/", form.get("action") or "/")
                if not self._is_own_host(action):
                    continue
                data = {i["name"]: i.get("value", "") for i in form.find_all("input") if i.get("name") and (i.get("type") or "").lower() == "hidden"}
                data[text_inputs[0]["name"]] = query
                method = (form.get("method") or "get").upper()
                reqs.append((method, action, data))
        q = quote_plus(query)
        reqs.append(("GET", f"{self.base}/?s={q}", None))
        reqs.append(("POST", f"{self.base}/index.php?do=search", {"do": "search", "subaction": "search", "story": query}))
        reqs.append(("GET", f"{self.base}/search/{q}/", None))
        return reqs

    def search(self, query, limit=10, crawl_pages=15):
        tokens = normalize_text(query).split()
        if not tokens:
            return []

        def matches(g):
            hay = normalize_text(g.title + " " + urlparse(g.url).path)
            return all(t in hay for t in tokens)

        results, seen = [], set()

        def add(games):
            for g in games:
                key = g.url.rstrip("/")
                if key not in seen and matches(g):
                    seen.add(key)
                    results.append(g)

        tried = set()
        for method, url, data in self._search_requests(query):
            sig = (method, url, tuple(sorted((data or {}).items())))
            if sig in tried:
                continue
            tried.add(sig)
            if method == "GET" and data:
                sep = "&" if "?" in url else "?"
                url = url + sep + "&".join(f"{k}={quote_plus(str(v))}" for k, v in data.items())
                data = None
            html = self.get(url, method=method, data=data, allow_error=True)
            if html:
                before = len(results)
                add(self.extract_games(html, url))
                if len(results) > before:
                    break

        # Fallback (or top-up): walk the latest pages and filter by title.
        if len(results) < limit:
            for n in range(1, crawl_pages + 1):
                html = self.get(self._page_url(n), allow_error=True)
                if not html:
                    break
                add(self.extract_games(html, self._page_url(n)))
                if len(results) >= limit:
                    break
        return self._sort_newest_first(results)[:limit]

    # -------------------------------------------------------------- game page
    def fetch_game(self, url):
        html = self.get(url)
        if not html:
            return None, []
        soup = BeautifulSoup(html, "html.parser")
        title = ""
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = og["content"]
        elif soup.find("h1"):
            title = soup.find("h1").get_text(" ", strip=True)
        elif soup.title:
            title = soup.title.get_text(" ", strip=True)
        game = Game(url, title)

        candidates = []
        for c in self._collect_embeds(soup, url, depth=0):
            if not c.get("gateway"):
                candidates.append(c)
                continue
            # "Watch" button to an intermediate page: the players are there.
            found = self._resolve_gateway(c["url"], url)
            if self.debug and found is not None:
                print_status(f"[debug] page intermédiaire {c['url']} : {len(found)} lecteur(s)", "info")
            if found is None:
                found = []
            elif not found:
                print_status(f"Aucun lecteur trouvé sur la page intermédiaire {c['url'][:70]}"
                             + ("" if self.debug else " (relancez avec --debug pour l'enregistrer)"), "warning")
            for sub in found:
                sub["label"] = " / ".join(p for p in (c["label"], sub["label"]) if p)
                sub["referer"] = c["url"]
                sub["section"] = c.get("section")
                candidates.append(sub)
            if not found:
                candidates.append(c)  # yt-dlp will still try the page itself
        servers = self._group(candidates)
        if self.debug:
            print_status(f"[debug] {len(candidates)} lecteur(s) détecté(s) :", "info")
            for c in candidates:
                print(f"    - label={c['label']!r:40} url={c['url']}")
        return game, servers

    def _resolve_gateway(self, url, referer):
        html = self.get(url, allow_error=True, referer=referer)
        if not html and self.last_status in (403, 429, 503):
            html = self._get_with_cloudscraper(url, referer)
        if not html:
            print_status(f"Page intermédiaire inaccessible ({self.last_status}) : {url[:70]}", "warning")
            return None
        return self._collect_embeds(BeautifulSoup(html, "html.parser"), url, depth=1, gateway=True)

    def _get_with_cloudscraper(self, url, referer):
        """Retry a page refused by an anti-bot (Cloudflare) check."""
        try:
            import cloudscraper
        except ImportError:
            return None
        try:
            scraper = cloudscraper.create_scraper()
            resp = scraper.get(url, headers={"Referer": referer}, timeout=30)
        except Exception as e:
            self.last_status = f"cloudscraper : {str(e)[:120]}"
            return None
        self.last_status = resp.status_code
        self._dump(url, resp.text, suffix="" if resp.status_code < 400 else f"_HTTP{resp.status_code}_cloudscraper")
        return resp.text if resp.status_code < 400 else None

    def _decode_wrapped_url(self, url):
        """Unwrap same-site redirectors ('/go?url=<base64>', '?link=https...')."""
        try:
            params = parse_qsl(urlparse(url).query)
        except ValueError:
            return url
        for _, v in params:
            if v.startswith(("http://", "https://", "//")):
                return v if not v.startswith("//") else "https:" + v
            try:
                padded = v + "=" * (-len(v) % 4)
                dec = base64.b64decode(padded, validate=False).decode("utf-8", "ignore")
                if dec.startswith(("http://", "https://")):
                    return dec
            except Exception:
                continue
        return url

    def _normalize_url(self, raw, page_url):
        if not raw:
            return None
        raw = raw.strip()
        if raw.startswith("<"):
            m = re.search(r'src=["\']([^"\']+)', raw)
            raw = m.group(1) if m else ""
        elif not raw.startswith(("http", "//", "/")) and re.search(r"https?://", raw):
            # onclick="loadPlayer('https://...')" and the like
            raw = re.search(r"https?://[^\s'\"<>)]+", raw).group(0)
        if not raw or raw.startswith(("javascript:", "#", "mailto:", "data:", "tel:")):
            return None
        if not raw.startswith(("http", "//", "/")):
            if len(raw) >= 16 and re.match(r"^[A-Za-z0-9+/=_-]+$", raw):
                try:
                    dec = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "ignore")
                    if dec.startswith(("http://", "https://", "//", "<iframe")):
                        return self._normalize_url(dec, page_url)
                except Exception:
                    pass
            if not re.match(r"^[\w./-]+$", raw):
                return None
        if raw.startswith("//"):
            raw = "https:" + raw
        url = urljoin(page_url, raw)
        if self._is_own_host(url):
            url = self._decode_wrapped_url(url)
        return url if url.startswith("http") else None

    @staticmethod
    def _looks_like_video_host(url):
        low = url.lower()
        host = (urlparse(url).hostname or "").lower()
        if _is_blocked(url):
            # vk video / google drive are legit hosts even though their
            # parent domain is otherwise blocked for share buttons.
            if not ("drive.google" in low or "vk.com/video" in low):
                return False
        if re.search(r"\.(jpe?g|png|gif|webp|svg|css|js|ico|woff2?)(\?|$)", low):
            return False
        return any(k in host for k in _KNOWN_VIDEO_HOSTS) or bool(_EMBED_PATH.search(urlparse(url).path))

    def _tab_labels(self, soup):
        """Map tab-pane ids to the text of the tab button that shows them."""
        labels = {}
        for el in soup.find_all(True):
            text = el.get_text(" ", strip=True)
            if not text or len(text) > 60:
                continue
            for attr in ("href", "data-target", "data-bs-target", "aria-controls", "data-tab", "data-id", "data-toggle-target"):
                val = el.get(attr)
                if isinstance(val, str) and val and (attr != "href" or val.startswith("#")):
                    labels.setdefault(val.lstrip("#"), text)
        return labels

    def _collect_embeds(self, soup, page_url, depth, gateway=False):
        """Players on a page, in page order. gateway=True parses a third-party
        intermediate page: only iframes and known video hosts count there,
        since everything else is that site's own content."""
        page_host = _strip_www((urlparse(page_url).hostname or "").lower())
        tab_labels = self._tab_labels(soup)
        candidates = []
        seen = set()
        # Headings seen so far above the current element: the game
        # ("USA vs France - FINAL", when a page holds several games), the
        # server ("Server #1 (DM)") and the part ("Part 2"). Button texts
        # don't count, or "Part 1" would leak onto the next button.
        ctx = {"section": None, "server": None, "part": None}
        follow = []

        def in_button(node):
            for i, parent in enumerate(node.parents):
                if i > 5 or parent is None:
                    break
                if parent.name in ("a", "button", "option"):
                    return True
            return False

        def is_heading(node):
            for i, parent in enumerate(node.parents):
                if i > 4 or parent is None:
                    break
                if parent.name in ("b", "strong", "h1", "h2", "h3", "h4", "h5"):
                    return True
            return False

        def ctx_label():
            return " / ".join(p for p in (ctx["server"], ctx["part"]) if p) or None

        def pane_label(el):
            for parent in [el] + list(el.parents):
                if not isinstance(parent, Tag):
                    continue
                pid = parent.get("id")
                if pid and pid in tab_labels:
                    return tab_labels[pid]
                for attr in ("data-tab", "data-id", "data-content"):
                    v = parent.get(attr)
                    if isinstance(v, str) and v in tab_labels and parent is not el:
                        return tab_labels[v]
            return None

        def own_label(el):
            texts = []
            for attr in ("title", "data-title", "data-name", "aria-label", "alt"):
                if el.get(attr):
                    texts.append(el[attr])
            if el.name != "iframe":
                t = el.get_text(" ", strip=True)
                if t and len(t) <= 80:
                    texts.append(t)
            label = " ".join(texts).strip()
            return label if label and _LABEL_HINT.search(label) else None

        by_key = {}

        def add(url, el):
            key = url.rstrip("/")
            if key in seen:
                # Same player seen again (e.g. a preview iframe, then its
                # "Part 1" button): keep the more informative label.
                prev = by_key.get(key)
                label = own_label(el)
                if prev is not None and label and _part_number(label) is not None and _part_number(prev["label"]) is None:
                    prev["label"] = " / ".join(p for p in (pane_label(el), label) if p)
                return
            seen.add(key)
            label = own_label(el) if el.name != "script" else None
            pane = pane_label(el)
            parts = [p for p in (pane, label) if p]
            joined = " ".join(parts)
            # The headings above the player fill in whatever the element
            # itself doesn't say (e.g. a "Part 2" heading followed by one
            # button per host, or "Server #1" followed by "Part N" buttons).
            if ctx["server"] and not _server_label(joined):
                parts.append(ctx["server"])
            if ctx["part"] and _part_number(joined) is None:
                parts.append(ctx["part"])
            cand = {"url": url, "label": " / ".join(dict.fromkeys(parts)), "host": host_display_name(url),
                    "section": ctx["section"]}
            by_key[key] = cand
            candidates.append(cand)

        for node in soup.descendants:
            if isinstance(node, NavigableString):
                text = re.sub(r"\s+", " ", str(node)).strip()
                if not text or len(text) > 100 or not node.parent or node.parent.name in ("script", "style", "title") \
                        or isinstance(node, Comment) or in_button(node):
                    continue
                if _server_label(text):
                    ctx["server"], ctx["part"] = text, None
                elif _part_number(text) is not None and _LABEL_HINT.search(text):
                    ctx["part"] = text
                elif depth == 0 and not gateway and re.search(r"\bvs\.?\s", text, re.IGNORECASE) \
                        and is_heading(node) and not _in_chrome(node, strict=False):
                    ctx.update(section=text, server=None, part=None)
                continue
            if not isinstance(node, Tag) or node.name in ("script", "style", "head", "meta", "link"):
                if isinstance(node, Tag) and node.name == "script" and node.string and (depth == 0 or gateway):
                    for m in re.finditer(r"(?:https?:)?//[^\s'\"<>\\]+", node.string):
                        u = self._normalize_url(m.group(0).replace("\\/", "/"), page_url)
                        host = (urlparse(u).hostname or "").lower() if u else ""
                        if u and not self._is_own_host(u) and any(k in host for k in _KNOWN_VIDEO_HOSTS) \
                                and self._looks_like_video_host(u) and _EMBED_PATH.search(urlparse(u).path):
                            add(u, node)
                continue
            # On an intermediate page (a blog), headers/sidebars hold social
            # links (youtube.com, facebook...) that aren't the player.
            if _in_chrome(node, strict=gateway):
                continue

            if node.name in ("iframe", "video", "source", "embed"):
                attrs = ("src", "data-src", "data-lazy-src", "data-litespeed-src", "data-original", "data-url")
            else:
                attrs = ("href", "data-src", "data-url", "data-link", "data-embed", "data-video", "data-iframe",
                         "data-href", "data-frame", "data-player", "data-server", "value", "onclick")
            for attr in attrs:
                val = node.get(attr)
                if not isinstance(val, str) or not val.strip():
                    continue
                url = self._normalize_url(val, page_url)
                if not url:
                    continue
                url_host = _strip_www((urlparse(url).hostname or "").lower())
                if gateway:
                    if _is_blocked(url):
                        continue
                    # A same-domain iframe can be the player wrapper itself;
                    # links only count when they point at an embed/video path
                    # (not "https://www.youtube.com/" social buttons).
                    if node.name in ("iframe", "video", "source", "embed") or \
                            url_host != page_host and any(k in url_host for k in _KNOWN_VIDEO_HOSTS) \
                            and _EMBED_PATH.search(urlparse(url).path) and self._looks_like_video_host(url):
                        add(url, node)
                    continue
                if self._is_own_host(url):
                    # A same-site player page (iframe) or a "Part 2" page:
                    # follow it once and collect what it embeds.
                    is_part_link = node.name == "a" and _PART_RE.search(node.get_text(" ", strip=True) or "")
                    if depth == 0 and (node.name == "iframe" or is_part_link) and url.split("#")[0].rstrip("/") != page_url.rstrip("/"):
                        follow.append((url, own_label(node) or pane_label(node) or ctx_label(), ctx["section"]))
                    continue
                if node.name in ("iframe", "video", "source", "embed"):
                    if not _is_blocked(url) or "drive.google" in url:
                        add(url, node)
                elif self._looks_like_video_host(url):
                    add(url, node)
                elif depth == 0 and not _is_blocked(url) and node.name in ("a", "button", "li", "option", "span", "div") \
                        and (_PART_RE.search(node.get_text(" ", strip=True) or "")
                             or node.name == "a" and _WATCH_TEXT.search(node.get_text(" ", strip=True) or "")):
                    # "Watch" / "Part 1" button to a page on another site
                    # that holds the actual player.
                    add(url, node)
                    if url.rstrip("/") in by_key:
                        by_key[url.rstrip("/")]["gateway"] = True

        for url, label, section in follow:
            html = self.get(url, allow_error=True)
            if not html:
                continue
            sub = self._collect_embeds(BeautifulSoup(html, "html.parser"), url, depth + 1)
            for c in sub:
                if c["url"].rstrip("/") in seen:
                    continue
                seen.add(c["url"].rstrip("/"))
                if label and label not in c["label"]:
                    c["label"] = " / ".join(p for p in (label, c["label"]) if p)
                c["section"] = section
                candidates.append(c)
        return candidates

    @staticmethod
    def _group(candidates):
        servers = {}
        for c in candidates:
            name = _server_label(c["label"]) or c["host"]
            key = (c.get("section"), name)
            if key not in servers:
                servers[key] = Server(name, c["host"], c.get("section"))
            srv = servers[key]
            if c["host"] not in srv.host.split(" / "):
                srv.host = f"{srv.host} / {c['host']}"
            number = _part_number(c["label"])
            srv.parts.append(Part(c["url"], c["label"], number, c.get("referer")))

        result = []
        for srv in servers.values():
            # Unlabelled players take the lowest free part number in page
            # order (the main iframe above a "Part 2" link is part 1), then
            # parts are listed by number. Parts are picked by their position
            # in that list ("1-2-3"); the number is also used to find the
            # same part on another server if this one fails.
            used = {p.number for p in srv.parts if p.number is not None}
            for p in srv.parts:
                if p.number is None:
                    n = 1
                    while n in used:
                        n += 1
                    p.number = n
                    used.add(n)
                    p.label = f"Part {n}" + (f" ({p.label})" if p.label else "")
            for p in srv.parts:
                # The server name is already shown as the list's title.
                short = _SERVER_RE.sub("", p.label)
                short = re.sub(r"\(\s*\)|#", "", short)
                short = re.sub(r"\s*/\s*/\s*|^\s*/\s*|\s*/\s*$", " ", short).strip(" -/")
                p.label = short or f"Part {p.number}"
            srv.parts.sort(key=lambda p: p.number)
            result.append(srv)
        return result
