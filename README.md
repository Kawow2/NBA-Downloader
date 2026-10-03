# 🎌🏀🎬 Anime · NBA · Films & Séries → Plex

Un seul programme, un menu au démarrage :

- **🎌 Anime** : le téléchargeur Anime-Sama / Nakanime de [SertraFurr/anime-sama-nakanime-downloader](https://github.com/SertraFurr/anime-sama-nakanime-downloader) (documentation plus bas), avec en plus des **téléchargements en parallèle** ;
- **🏀 NBA** : les replays de [basketball-video.com](https://basketball-video.com) en **.mp4** rangés et nommés pour Plex ;
- **🎬 Films & Séries** : films et séries de [nakios.rent](https://nakios.rent) en **.mp4**, rangés en bibliothèques Plex **Films** et **Séries**.

Chaque catégorie a **son propre dossier** (sa bibliothèque Plex), demandé la première fois qu'on l'utilise et modifiable dans **Réglages**.

```
╔══════════════════════════════════════════════════════════════╗
║            ANIME · NBA · FILMS & SÉRIES  DOWNLOADER           ║
╚══════════════════════════════════════════════════════════════╝
  1. 🎌 Anime           (anime-sama, nakanime)  → /srv/plex/Anime
  2. 🏀 NBA             (basketball-video.com)  → /srv/plex/Sports/NBA
  3. 🎬 Films & Séries  (nakios.rent)           → /srv/plex
  4. ⚙️  Réglages (dossiers, parallélisme, qualité)
  q. Quitter
```

## Lancement

### Linux / macOS (ex. portable serveur Plex, via SSH)

```bash
sudo apt install git python3-venv ffmpeg tmux     # Debian/Ubuntu (dnf, pacman... selon la distribution)
git clone https://github.com/Kawow2/NBA-Downloader.git && cd NBA-Downloader
./start.sh --tmux
```

`start.sh` crée l'environnement virtuel `.venv` au premier lancement, y installe `requirements.txt`, puis lance `main.py`.
Avec `--tmux`, le programme tourne dans une session tmux : **si la connexion SSH coupe, les téléchargements continuent**. Se détacher : `Ctrl+B` puis `D` ; revenir : `./start.sh --tmux`.

### Windows (PowerShell)

Prérequis : Python 3.8+ (`winget install Python.Python.3.12`) et, fortement conseillé, ffmpeg (`winget install Gyan.FFmpeg`).

```powershell
cd C:\chemin\vers\NBA-Downloader
.\start.ps1
```

Si Windows refuse d'exécuter le script : `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Équivalent manuel (les deux systèmes) : `python -m venv .venv`, activer le venv, `pip install -r requirements.txt`, `python main.py`.

### Raccourcis

| Commande | Rôle |
| --- | --- |
| `python main.py` | menu Anime / NBA / Films & Séries |
| `python main.py anime [options]` | directement les animes (`--search`, `--url`, `--episodes`... voir plus bas) |
| `python main.py nba [options]` | directement la NBA (`--url`, `--quality`, `--threads`... voir plus bas) |
| `python main.py films [options]` | directement les films & séries (`--search`, `--url`, `--season`, `--episodes`... voir plus bas) |
| `python main.py --set-anime-dir DOSSIER` | dossier par défaut des animes |
| `python main.py --set-nba-dir DOSSIER` | dossier par défaut de la NBA |
| `python main.py --set-films-dir DOSSIER` | dossier par défaut des films & séries |
| `python main.py --faststart DOSSIER` | optimise les `.mp4` déjà téléchargés (voir « faststart » ci-dessous) |
| `--anime-dir` / `--nba-dir` / `--films-dir DOSSIER` | dossier pour ce lancement seulement (c'est ce que font les variables `CHEMIN_ANIME` / `CHEMIN_NBA` / `CHEMIN_FILMS` de `start.sh`, `$CheminAnime` / `$CheminNBA` / `$CheminFilms` de `start.ps1`) |

Sans `anime`/`nba`/`films`, les options propres à un seul programme (ex. `--quality`, `--season`), ou l'adresse donnée à `--url`, suffisent à choisir.

## Réglages (menu `4`)

| Réglage | Défaut |
| --- | --- |
| Dossier des animes | demandé au 1er lancement (proposé : `~/Videos/Anime`) |
| Dossier NBA | demandé au 1er lancement (proposé : `~/Videos/NBA`) |
| Dossier Films & Séries | demandé au 1er lancement (proposé : `~/Videos`, contient `Films/` et `Séries/`) |
| Anime : morceaux téléchargés en parallèle par épisode | 32 |
| Anime : épisodes téléchargés en même temps | 2 |
| NBA : morceaux téléchargés en parallèle | 32 |
| NBA : qualité max | 1080p |
| Films : morceaux téléchargés en parallèle | 32 |
| Films : qualité max | 1080p |
| Films : endpoint des lecteurs (avancé) | auto |

Tout est mémorisé dans `src/utils/config/config.json`. Le réglage `11` optimise les `.mp4` déjà présents dans les trois dossiers.

### Faststart (« Web Optimized »)

Chaque `.mp4` téléchargé (anime, NBA et films/séries) a son index au **début** du fichier : Plex démarre la lecture tout de suite au lieu de parcourir tout le fichier. Les fichiers qui ne l'ont pas sont réécrits automatiquement à la fin du téléchargement (copie sans ré-encodage, quelques secondes à une minute selon le disque). Pour les fichiers téléchargés avant cette version : `python main.py --faststart "/mnt/plexmedia/Vidéos"` (ou `4. Réglages` → `11`).

La même commande **répare le son** des épisodes anime convertis par les versions précédentes (son muet ou qui grésille) : la conversion `.ts` → `.mp4` y enregistrait une fréquence audio fausse (48000 Hz au lieu de 44100 Hz, par ex.). La vraie fréquence est retrouvée d'après la durée, et les données audio sont gardées telles quelles, sans ré-encodage.

## Serveur Plex sur un portable Linux

Disposition conseillée (deux bibliothèques Plex de type **Séries TV**) :

```
/srv/plex/Anime/                ← dossier des animes   = bibliothèque « Anime »
    Dragon Ball/Season 01/Dragon Ball - S01E01.mp4
    Dragon Ball/Specials/Dragon Ball - S00E01.mp4      (films, OAV)
/srv/plex/Sports/NBA/           ← dossier NBA          (bibliothèque « Sports » = /srv/plex/Sports)
    Season 2026/NBA - 2026-06-13 - New York Knicks vs San Antonio Spurs - NBA Finals Game 5.mp4
```

- Plex tourne sous l'utilisateur `plex` : il doit pouvoir lire ces dossiers, ex. `sudo chmod -R o+rX /srv/plex`.
- Anime-Sama peut demander le cookie Cloudflare `cf_clearance` et le User-Agent d'un navigateur : récupérez-les sur un PC **du même réseau** (le cookie est lié à l'adresse IP publique) avec le même navigateur.
- Les dossiers anime d'anciennes versions (`dragon-ball/saison1`) sont renommés automatiquement (`Dragon Ball/Season 01`, épisodes compris) au prochain téléchargement de cet anime ; un dossier `Season 1` créé par Sonarr est gardé tel quel.
- Identification Plex des animes (fichier `.match` MyAnimeList, ou tag TVDB/IMDb dans le nom du dossier) : réglage `3. Settings` du menu anime.

## Anime : téléchargements en parallèle

Par rapport au dépôt d'origine, sans rien avoir à répondre :

- les segments vidéo (lecteurs HLS : Vidmoly, VOE, Filemoon…) d'un épisode se téléchargent **32 à la fois** (avant : 10, et seulement en répondant « y »), sur des connexions réutilisées, avec des nouvelles tentatives espacées, et sont écrits sur le disque au fur et à mesure (avant : tout l'épisode restait en mémoire) ;
- les lecteurs à fichier unique (Sibnet, Sendvid…) se téléchargent sur **plusieurs connexions** à la fois (16 au plus), avec retour automatique à une seule connexion si l'hébergeur refuse ;
- les épisodes d'une saison se téléchargent **2 à la fois** ;
- la conversion `.ts` → `.mp4` utilise ffmpeg par défaut quand il est installé (plusieurs fois plus rapide que PyAV) ;
- la barre de chaque épisode affiche le débit (Mo/s) ;
- `Ctrl+C` arrête tout immédiatement.

Sur un serveur de test qui bride chaque connexion comme les hébergeurs : épisode HLS 10,7 s → 0,9 s, fichier unique 11,9 s → 2,5 s.

---

# 🏀 NBA

## Déroulement

1. **Menu** : `1` = les 10 derniers matchs du site, `2` = recherche (ex. `knicks spurs`) puis les 10 derniers résultats. On peut aussi coller directement l'URL d'un match.
2. **Match** : choisir le numéro dans la liste.
3. **Serveur** : choisir le serveur (VOE, Filemoon, OK.ru, Streamtape...).
4. **Parties** : taper les parties voulues, ex. `1-2-3` (= parties 1, 2 et 3), `2`, ou Entrée pour toutes.
5. **Chemin** : Entrée = dossier NBA, ou taper un autre chemin (le programme propose alors de le garder comme défaut).
6. **Nom du fichier** : le titre affiché dans la liste / la recherche (ou le match choisi sur une page qui en contient plusieurs). S'il ne suffit pas à identifier un match (pas de « A vs B » ou pas de date), le programme demande un nom.
7. **Téléchargement** en .mp4 (sans ré-encodage). Si plusieurs parties sont choisies, elles sont toujours fusionnées en un seul fichier. Les serveurs ne découpent pas le match de la même façon (3 parties sur l'un, 2 ou 1 sur l'autre) : si une partie échoue, le match est repris **en entier** depuis le serveur suivant, jamais complété avec une partie d'un autre serveur (sinon : morceaux en double, fichier de 4 h). Une fois le match terminé, ses fichiers temporaires (`.part`, `.ytdl`) sont supprimés ; ceux d'anciens téléchargements interrompus sont proposés à la suppression. Après un Ctrl+C, relancer le même match reprend le téléchargement.

## Organisation pour Plex

```
<dossier NBA>\Season 2026\NBA - 2026-06-13 - New York Knicks vs San Antonio Spurs - NBA Finals Game 5.mp4
```

Créez une bibliothèque Plex de type **Séries TV** sur le dossier qui **contient** le dossier NBA (ex. bibliothèque `/srv/plex/Sports` pour le dossier NBA `/srv/plex/Sports/NBA`). Plex reconnaît les épisodes datés (`AAAA-MM-JJ`). Les parties non fusionnées sont nommées `... - pt1.mp4`, `... - pt2.mp4`, que Plex regroupe.

## Options (`python main.py nba ...`)

| Option | Rôle |
| --- | --- |
| `--url <URL>` | télécharger directement ce match |
| `--dest <chemin>` | dossier de destination sans poser la question |
| `--site <URL>` | si le site change de domaine (mémorisé) |
| `--quality 720\|1080\|1440\|2160\|best` | qualité max (défaut 1080p, mémorisée) ; `best` ≈ 20 Go par match chez OK.ru |
| `--threads <N>` | morceaux téléchargés en parallèle (défaut 32, mémorisé) ; pour un fichier direct (.mp4, ex. OK.ru), nombre de connexions simultanées (16 max) |
| `--debug` | enregistre les pages dans `./debug` et affiche les lecteurs détectés |

Si aucun lecteur n'est détecté sur une page (changement de mise en page du site), relancez avec `--debug` : les pages HTML sauvegardées dans `./debug` permettent d'adapter `src/nba/site.py`.
Si le site est derrière Cloudflare, le programme demande le cookie `cf_clearance` et le User-Agent du navigateur. Si un hébergeur ne marche plus, mettez yt-dlp à jour : `.venv/bin/python -m pip install -U "yt-dlp[default,curl-cffi]"` (Windows : `.\.venv\Scripts\python.exe -m pip ...`).


---

# 🎬 Films & Séries

Films et séries depuis **[nakios.rent](https://nakios.rent)** (l'API du site est au format TMDB), en `.mp4` prêts pour Plex. Le téléchargement réutilise tout le moteur de la partie NBA (extracteurs VOE / Uqload / Filemoon / Vidmoly / Sibnet…, yt-dlp, multi-connexions, fusion, faststart).

## Déroulement

1. **Menu** : `1` = rechercher un film ou une série. On peut aussi coller directement une URL `nakios.rent` (`.../series/<id>` ou `.../film/<id>`).
2. **Résultat** : choisir le numéro ; chaque ligne indique **Film** ou **Série** et l'année.
3. **Série** : choisir la/les **saison(s)** (ex. `1`, `1-3`, Entrée = toutes), puis les **épisodes** (ex. `1-5`, `1,3,5`, Entrée = tous).
4. **Chemin** : Entrée = dossier Films & Séries, ou un autre chemin.
5. **Lecteur / qualité** : si le titre propose **plusieurs lecteurs**, la liste s'affiche (`lecteur · qualité · langue`, ex. `Uqload · 1080p · VOSTFR`) pour en choisir un, ou `0` = auto. Ils sont classés **meilleure qualité d'abord** ; pour une série, le choix est demandé **une fois** et s'applique à tous les épisodes. S'il n'y a qu'un lecteur (souvent un seul fichier par titre), rien n'est demandé.
6. **Téléchargement** en `.mp4` : le lecteur choisi est pris en priorité, les autres servent de secours en cas d'échec. Les fichiers déjà présents sont ignorés ; relancer reprend ce qui manque.

Les **qualités proposées sont celles réellement disponibles pour le titre** (lues dans les données du site et l'URL) : la liste des lecteurs affiche `lecteur · qualité · langue`, classée meilleure d'abord. Il n'y a donc pas de menu de « plafond » à choisir — tu prends directement ce qui existe. `--quality 720|1080|1440|2160|best` (ou le réglage `9`) reste disponible comme plafond facultatif (défaut 1080p), surtout utile pour les lecteurs gérés par yt-dlp ; une source en **fichier direct** (un seul `.mp4`) garde la résolution de son fichier quoi qu'il arrive.

## Organisation pour Plex

Le dossier choisi est le **parent** de deux bibliothèques :

```
<dossier>/Films/Inception (2010)/Inception (2010).mp4
<dossier>/Séries/Chernobyl (2019)/Season 01/Chernobyl (2019) - S01E03 - Open Wide, O Earth.mp4
```

Créez une bibliothèque Plex **Films** (agent « Films ») sur `<dossier>/Films` et une bibliothèque **Séries TV** sur `<dossier>/Séries`.

## Options (`python main.py films ...`)

| Option | Rôle |
| --- | --- |
| `--search "<titre>"` | recherche directe |
| `--url <URL>` | film / série directement (`.../series/<id>`, `.../film/<id>`) |
| `--season <N>` | saison(s) d'une série (ex. `1`, `1-3`, `all`) |
| `--episodes <N>` | épisode(s) (ex. `1-5`, `1,3,5`, `all`) |
| `--dest <chemin>` | dossier de destination sans poser la question |
| `--quality 720\|1080\|1440\|2160\|best` | qualité max (défaut 1080p, mémorisée) |
| `--threads <N>` | morceaux/connexions en parallèle (défaut 32, mémorisé) |
| `--site <URL>` | si le site change de domaine (mémorisé) |
| `--browser-visible` / `--no-browser` | navigateur de session visible / désactivé (voir ci-dessous) |
| `--cookie "<cookie>"` / `--user-agent "<ua>"` | session passée à la main (repli, mémorisés) — voir ci-dessous |
| `--profile-id <id>` | valeur de l'en-tête `x-profile-id` (mémorisée) |
| `--sources-path <gabarit>` | endpoint des lecteurs, ex. `/api/movie/{id}/sources` (mémorisé) |
| `--debug` | enregistre les réponses de l'API dans `./debug` |

### Session (Cloudflare) — automatique

Le site est derrière **Cloudflare** et son API n'ouvre qu'avec une **session** (le `credentials: include` du site). Le programme s'en occupe **tout seul** : au lancement, il ouvre un **navigateur headless** ([Playwright](https://playwright.dev)) qui passe Cloudflare, récupère les cookies (`cf_clearance`, `nk_verified`) et le User-Agent, les mémorise dans `config.json` (non versionné) et les **renouvelle automatiquement** à l'expiration. **Aucun cookie à coller.**

Playwright n'est à installer **qu'une fois** (le programme propose de le faire au premier lancement), sinon à la main :

```bash
.venv/bin/python -m pip install playwright && .venv/bin/python -m playwright install chromium
```

Si Cloudflare bloque le mode **invisible** (il détecte souvent le navigateur headless), le programme bascule tout seul, puis :

- **PC avec écran** : `--browser-visible` ouvre un vrai navigateur visible (passe le challenge bien plus souvent).
- **Serveur Linux sans écran** : il relance automatiquement un navigateur visible **dans un écran virtuel** si ces deux-là sont installés :
  ```bash
  sudo apt install xvfb && .venv/bin/python -m pip install pyvirtualdisplay
  ```
- `--no-browser` : désactive le navigateur automatique.

Si rien ne passe (API toujours franchie par le navigateur mais flux refusé), le navigateur **reste ouvert** et sert directement d'API le temps de la session.

**Repli manuel** (Playwright indisponible) : passe la session toi-même avec `--cookie "…" --user-agent "…"` (ou quand le programme le demande) — `F12 → Réseau` → une requête `/api/…` → en-tête `cookie` ; et `F12 → Console → navigator.userAgent`. Le `cf_clearance` est lié à ton IP + navigateur et expire ; c'est un secret, gardé en local, jamais commité.

### Serveur Plex en SSH (sans navigateur) → jeton de session

Un serveur headless (portable Linux auquel tu te connectes en SSH) n'a pas de navigateur pour franchir Cloudflare. Comme le `cf_clearance` est lié à l'**IP publique**, capture la session sur une **machine du même réseau qui a un navigateur** (ton PC fixe) et transfère-la :

1. **Sur le PC fixe** (même réseau que le serveur) :
   ```
   python main.py films --export-session          # (ou --export-session --browser-visible)
   ```
   Un navigateur s'ouvre, franchit Cloudflare, et le programme imprime une commande `--import-session <jeton>` (le jeton contient cookie, User-Agent, base d'API, profil).
2. **Sur le serveur** (SSH), colle la commande affichée :
   ```
   python main.py films --import-session <jeton>
   ```
   Le serveur mémorise la session et vérifie que l'API répond. Ensuite, télécharge normalement — ajoute `--no-browser` pour qu'il n'essaie jamais d'ouvrir un navigateur :
   ```
   ./start.sh films --no-browser
   ```

Le cookie expire au bout de quelques jours (ou si ton IP publique change) : refais l'étape 1 + 2 avec un jeton frais. Le jeton est un secret (il contient ta session) — transfère-le par ton SSH, ne le partage pas.

> Alternative sans jeton : `ssh -X` avec un serveur X sur le PC fixe (VcXsrv sous Windows, XQuartz sous macOS) affiche le navigateur de `--browser-visible` sur ton écran à travers le SSH.

### Si « aucun lecteur trouvé »

La **recherche**, les **détails** et la **liste des saisons/épisodes** suivent l'API TMDB (trouvées automatiquement). En revanche, l'endpoint qui renvoie les **lecteurs vidéo** est propre au site : plusieurs chemins courants sont essayés, mais s'ils échouent, indiquez le bon :

1. Ouvrez un film / un épisode sur le site, `F12 → onglet Réseau`, lancez la **lecture**.
2. Repérez la requête `/api/...` dont la réponse contient les lecteurs (URL `voe` / `uqload` / `filemoon`… ou un `.m3u8` / `.mp4`).
3. `4. Réglages` → `10` (ou `--sources-path`), en remplaçant l'id par `{id}` (et, pour les séries, la saison / l'épisode par `{season}` / `{episode}`). Ex. `/api/movie/{id}/sources`, `/api/tv/{id}/season/{season}/episode/{episode}/sources`.

(Si c'est la **recherche** elle-même qui échoue avec « cookie de session requis », voir « Cookie de session » ci-dessus.) `--debug` enregistre les réponses dans `./debug` pour trouver le bon chemin.


---

<div align="center"> 

# Anime Downloader
  
<img src="https://img.shields.io/badge/Python-3.6+-blue.svg?style=for-the-badge&logo=python" alt="Python Version">
<img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux_(mostly_windows)-lightgrey.svg?style=for-the-badge" alt="Platform">
<img src="https://img.shields.io/badge/License-GPL_3-green.svg?style=for-the-badge" alt="License">
 
**A powerful, beautiful and simple CLI tool to download anime episodes from Anime-Sama & Nakanime. (More coming)**

(✨53 STARS✨! Thanks!) 

*Questions? Unworking URLs? Open an issue, will be added fastly (hopefully)*

It also works on unreleased episodes! (Where it says 'This content does not exist.' - sometimes it still exists.)

### 🌟 Star this repo if it helped you!

Looking for projects to do! Feel free to request in issues!

![Website Support](https://img.shields.io/badge/Website%20Support-100%25-brightgreen)

## ✨ Features

### Supports videos/scans

<table>
<tr>
<td width="50%">

###  **Smart & Intuitive**
-  **Beautiful CLI Interface** with colors and emojis
-  **Auto URL Validation** with helpful error messages
-  **Built-in Tutorial** for first-time users
-  **Multi-threaded Downloads** for blazing fast performance
-  **Automatic Fallback** to next player if download fails

</td>
<td width="50%">

###  **Powerful & Reliable**  
-  **Multiple Player Support** with smart fallback chain
-  **12 Video Sources** supported (SendVid, Sibnet, VOE, Filemoon, LuluStream, Vidzy, Uqload, Vidmoly and more)
-  **Real-time Progress** with download speeds
-  **Robust Error Handling** with retry logic
-  **Multiple Episode Selection** with thread support
-  **FFmpeg support** - choose between 2 converters

</td>
</tr>
</table>

---

## Quick Start
</div>

### 📋 Prerequisites

<details>
<summary> <strong> <align="center">Python Requirements</strong></summary>

Make sure you have **Python 3.6+** installed:

```bash
# Check Python version
python --version

# Install required packages
pip install requests beautifulsoup4 tqdm
```

**Required Libraries:**
- `requests` - HTTP requests handling
- `beautifulsoup4` - HTML parsing
- `tqdm` - Progress bar display

</details>


### Installation & Usage

```bash
# 1. Clone the repository.
git clone https://github.com/SertraFurr/Anime-Downloader.git

# 2. Navigate into the project directory.
cd Anime-Downloader

# 3. Run it.
python3 main.py

# Or use the CLI arguments.
python3 main.py anime --help
```

---

## CLI Arguments & Usage

You can use the script entirely from the command line without interactive prompts.

| Argument | Description | Example | Default |
| :--- | :--- | :--- | :--- |
| `--search` | Search for an anime by name | `--search "naruto"` | `None` |
| `--url` | Direct URL to anime season/page | `--url "https://..."` | `None` |
| `--episodes` | Select episodes to download | `--episodes "1,2"` | `None` |
| `--player` | Select specific player (fuzzy match) | `--player "Sibnet"` | `None` |
| `--dest` | Base download directory (auto-creates folders) | `--dest "C:/X"` | `Config Folder` |
| `--threads` | Enable threaded episode downloads | `--threads` | `False` |
| `--fast` | Enable multi-threaded .ts download (10x faster) | `--fast` | `False` |
| `--mp4` | Auto-convert .ts to .mp4 | `--mp4` | `False` |
| `--tool` | Select conversion tool (av/ffmpeg) | `--tool av` | `av` |
| `--latest` | Download only the latest episode | `--latest` | `False` |
| `--no-mal` | Disable MyAnimeList research | `--no-mal` | `False` |


### User Examples

**1. Search and Download Interactively:**
```bash
python main.py anime --search "roshidere"
```

**2. Download Specific Episodes from URL (Fast Mode):**
```bash
python main.py anime --url "https://anime-sama.tv/catalogue/roshidere/saison1/vostfr/" --episodes "1,2" --fast --mp4
```

**3. Download ALL episodes from a specific player:**
```bash
python main.py anime --search "one piece" --player "Sibnet" --episodes "all" --threads
```

---

<div align="center">


## Complete Interactive Usage Guide


<h3>Three Simple Steps</h3>


<table>
<tr>
<td width="33%" align="center">

###  Find Anime
<img src="https://img.shields.io/badge/Step-1-blue?style=for-the-badge">

Visit **[Anime-Sama](https://anime-sama.fr/catalogue/)** or **[Nakanime](https://nakanime.tv/)**

- Search your anime  
- Select season & language  
-  Copy the complete URL

</td>
<td width="33%" align="center">

### Run Script  
<img src="https://img.shields.io/badge/Step-2-green?style=for-the-badge">

Launch the downloader

- Paste the URL  
- Choose player & episode  
- Set download folder

</td>
<td width="33%" align="center">

### Enjoy!
<img src="https://img.shields.io/badge/Step-3-purple?style=for-the-badge">

Watch the magic happen

- Auto-download starts  
- Real-time progress  
- Episode ready to watch!

</td>
</tr>
</table>

</div>

<details>
<summary>🔗 Example URLs</summary>

**Anime-Sama - Works**
```
- https://anime-sama.fr/catalogue/roshidere/saison1/vostfr/
- https://anime-sama.fr/catalogue/demon-slayer/saison1/vf/
- https://anime-sama.fr/catalogue/attack-on-titan/saison3/vostfr/
- https://anime-sama.fr/catalogue/one-piece/saison1/vostfr/
```

**Nakanime - Works**
```
- https://nakanime.tv/anime/roshidere/
- https://nakanime.tv/anime/demon-slayer/
```

**Won't work**
```
- https://anime-sama.fr/catalogue/roshidere/
- https://anime-sama.fr/
```
</details>


## 🛠️ Video Source Support

> **⚠️ Threaded mode** is only suitable for strong Wi-Fi connections that won't crash when handling multiple simultaneous downloads.

| Platform | Status | Type | Notes |
|:--------:|:------:|:----:|:------|
| 📹 **SendVid** | ![Working](https://img.shields.io/badge/Status-✅_Working-brightgreen) | Direct MP4 | Primary recommended source |
| 🎬 **Sibnet** | ![Working](https://img.shields.io/badge/Status-✅_Working-brightgreen) | Direct MP4 | Reliable backup source |
| 🎬 **VOE** | ![Working](https://img.shields.io/badge/Status-✅_Working-brightgreen) | HLS (.ts→mp4) | Fast with `--fast` flag |
| 🎬 **Filemoon** | ![Working](https://img.shields.io/badge/Status-✅_Working-brightgreen) | HLS (.ts→mp4) | Fast with `--fast` flag |
| 🎬 **LuluStream / Luluvdo** | ![Working](https://img.shields.io/badge/Status-✅_Working-brightgreen) | HLS (.ts→mp4) | Token freshly resolved at download time |
| 🎬 **Vidzy** | ![Working](https://img.shields.io/badge/Status-✅_Working-brightgreen) | HLS (.ts→mp4) | XOR-decrypted stream |
| 🎬 **Uqload** | ![Working](https://img.shields.io/badge/Status-✅_Working-brightgreen) | HLS (.ts→mp4) | JS-unpacked stream |
| 🎬 **Vidmoly** | ![Working](https://img.shields.io/badge/Status-✅_Working-brightgreen) | HLS (.ts→mp4) | SLOW without `--fast`. Very fast with it |
| 🎬 **Vidzy.live** | ![Working](https://img.shields.io/badge/Status-✅_Working-brightgreen) | HLS (.ts→mp4) | Alias for Vidzy |
| 🎬 **Embed4Me** | ![Working](https://img.shields.io/badge/Status-✅_Working-brightgreen) | HLS (.ts→mp4) | Download .ts then convert to mp4 |
| 🎬 **AnsEmbed** | ![Working](https://img.shields.io/badge/Status-✅_Working-brightgreen) | HLS (.ts→mp4) | Download .ts then convert to mp4 |
| 🎬 **OneUpload** | ![Working](https://img.shields.io/badge/Status-✅_Working-brightgreen) | HLS (.ts→mp4) | Download .ts then convert to mp4 |
| 🎬 **MovearnPre** | ![Inconsistent](https://img.shields.io/badge/Status-➖_Inconsistent-orange) | HLS (.ts→mp4) | Download .ts then convert to mp4. INCONSISTENT |
| 🎬 **SmoothPre** | ![Inconsistent](https://img.shields.io/badge/Status-➖_Inconsistent-orange) | HLS (.ts→mp4) | Download .ts then convert to mp4. INCONSISTENT |
| 🎬 **Mivalyo** | ![Inconsistent](https://img.shields.io/badge/Status-➖_Inconsistent-orange) | HLS (.ts→mp4) | Download .ts then convert to mp4. INCONSISTENT |
| 🎬 **Dingtezuni** | ![Inconsistent](https://img.shields.io/badge/Status-➖_Inconsistent-orange) | HLS (.ts→mp4) | Download .ts then convert to mp4. INCONSISTENT |
| 🚫 **MYVI** | ![Deprecated](https://img.shields.io/badge/Status-❌_Deprecated-red) | - | Malicious - only redirects to ads |
| 🚫 **Minochinos** | ![Unsupported](https://img.shields.io/badge/Status-❌_Unsupported-red) | - | Useless to implement |
| 🤔 **VK.com** | ![Unsupported](https://img.shields.io/badge/Status-❌_Unsupported-red) | - | Could try, but no working URLs found |

---


## Screenshots

<details>
<summary>🖼️ <strong>View CLI Interface Screenshots</strong></summary>

###  Main Interface
```
╔══════════════════════════════════════════════════════════════╗
║                    ANIME VIDEO DOWNLOADER                    ║
║                       Enhanced CLI v2.0                      ║
╚══════════════════════════════════════════════════════════════╝

📺 Download anime episodes from Anime-Sama & Nakanime!
```

###  Player Selection
```
🎮 SELECT PLAYER
─────────────────────────────────────────────────────────────────
  1. Player 1 (12/15 working episodes)
  2. Player 2 (8/15 working episodes)  
  3. Player 3 (15/15 working episodes)

Enter player number (1-3) or type player name:
```

###  Download Progress
```
⬇️ DOWNLOADING
─────────────────────────────────────────────────────────────────
📥 roshidere_episode_1.mp4: 100%|████████| 145M/145M [02:15<00:00, 1.07MB/s]
✅ Download completed successfully!
```

</details>

---


## ⚙️ Configuration



<details>
<summary> <strong>Customization Options</strong></summary>


### Default Settings

**Download Directory**: `./videos/`

**Video Format**: `.mp4`

**Naming Convention**: `{anime_name}_episode_{number}.mp4`


###  Color Themes
The script uses a beautiful color scheme:

🔵 **Info**: Cyan messages

✅ **Success**: Green confirmations  

⚠️ **Warning**: Yellow alerts

❌ **Error**: Red error messages

💜 **Headers**: Purple titles


</details>

---

## 🤝 Contributing

We welcome contributions! Here's how you can help:

[![Issues](https://img.shields.io/badge/Issues-Welcome-blue?style=for-the-badge)](https://github.com/sertrafurr/Anime-Downloader/issues)
[![Pull Requests](https://img.shields.io/badge/PRs-Welcome-green?style=for-the-badge)](https://github.com/sertrafurr/Anime-Downloader/pulls)
[![Discussions](https://img.shields.io/badge/Discussions-Join-purple?style=for-the-badge)](https://github.com/sertrafurr/Anime-Downloader/discussions)


### 🐛 Found a Bug?
 Check existing [issues](https://github.com/sertrafurr/Anime-Downloader/issues)
 Create a new issue with:
   📝 Clear description
   🔄 Steps to reproduce
   💻 System information

### 💡 Feature Request?
 Open a [discussion](https://github.com/sertrafurr/Anime-Downloader/discussions)
 Explain your idea
 Community feedback welcome!


---


## 📄 License

This project is licensed under the **GPL v3 License**

[![License: GPL](https://img.shields.io/badge/License-GPL_V3-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)

*Feel free to use, modify, and distribute!*


---


## ⚠️ Disclaimer

**📢 Important Notice**

 🎯 This tool is for **educational purposes** only

 📺 Respect **copyright laws** in your jurisdiction  

---


## 🙏 Acknowledgments

<img src="https://img.shields.io/badge/Made_with-❤️-red?style=for-the-badge">

---

### 🌟 Star this repo if it helped you!

[![Stars](https://img.shields.io/github/stars/sertrafurr/anime-downloader?style=for-the-badge&logo=github)](https://github.com/sertrafurr/anime-downloader/stargazers)

You wish for something/a service to get removed/added, open an issue.
