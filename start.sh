#!/usr/bin/env bash
# Anime & NBA Downloader - Linux/macOS launcher: runs main.py in a virtual
# environment (.venv), created on first run.
#
#   ./start.sh                  menu: Anime, NBA or Films & Séries
#                               (over SSH: runs inside tmux automatically, so
#                               the downloads survive a dropped connection or
#                               the client PC being switched off — reattach
#                               later with ./start.sh again)
#   ./start.sh --tmux           force the tmux session (also works locally)
#   ./start.sh --no-tmux        force a plain run, even over SSH
#   ./start.sh nba --url "https://basketball-video.com/..."
#   ./start.sh films --url "https://nakios.rent/series/87108"
#
# Push notification when the program finishes: set NTFY_TOPIC below.
#
# Manual equivalent:
#   python3 -m venv .venv && . .venv/bin/activate
#   pip install -r requirements.txt && python main.py

# Folders (Plex libraries) for this launcher, e.g.
#   CHEMIN_ANIME="/srv/plex/Anime"   CHEMIN_NBA="/srv/plex/NBA"
#   CHEMIN_FILMS="/srv/plex"   (contient Films/ et Séries/ = 2 bibliothèques)
# Empty: the folders chosen in the program's menu (Réglages), asked the
# first time each category is used.
CHEMIN_ANIME=""
CHEMIN_NBA=""
CHEMIN_FILMS=""

# Push notification via ntfy when the program finishes (optional).
#   1. Install the "ntfy" app on your phone (iOS/Android), or open ntfy.sh.
#   2. Pick a long, HARD-TO-GUESS topic name — it works like a password:
#      anyone who knows it can read (and send) your notifications.
#   3. Subscribe to that topic in the app, then write it below (or export
#      NTFY_TOPIC before launching). Leave empty to disable notifications.
NTFY_TOPIC="${NTFY_TOPIC:-}"
NTFY_SERVER="${NTFY_SERVER:-https://ntfy.sh}"

# Send a "download finished" push via ntfy. No-op if NTFY_TOPIC is empty or
# curl is missing; never fails the run.
notify_done() {
    local status="${1:-0}" body tags prio
    [ -n "$NTFY_TOPIC" ] || return 0
    command -v curl >/dev/null 2>&1 || return 0
    if [ "$status" -eq 0 ]; then
        body="Téléchargement terminé ✅ (sur $(hostname 2>/dev/null))"
        tags="white_check_mark"; prio="default"
    else
        body="Arrêté avec le code $status ⚠️ (sur $(hostname 2>/dev/null))"
        tags="warning"; prio="high"
    fi
    curl -fsS --max-time 15 \
        -H "Title: NBA Downloader" -H "Tags: $tags" -H "Priority: $prio" \
        -d "$body" "$NTFY_SERVER/$NTFY_TOPIC" >/dev/null 2>&1 || true
}

cd "$(dirname "$0")" || exit 1

# Over SSH, run inside a persistent tmux session by default so downloads keep
# going if the connection drops (or the client PC is switched off). --tmux
# forces it everywhere; --no-tmux opts out.
want_tmux=""
if [ "$1" = "--tmux" ]; then
    shift; want_tmux=1
elif [ "$1" = "--no-tmux" ]; then
    shift
elif [ -n "$SSH_CONNECTION" ] && [ -z "$STY" ]; then
    want_tmux=1
fi

if [ -n "$want_tmux" ] && [ -z "$TMUX" ] && [ -z "$STY" ]; then
    if command -v tmux >/dev/null 2>&1; then
        # -A: reattach to the running session if there is one.
        # START_SH_IN_TMUX: pause at the end, or tmux would close the
        # window (and any error message) as soon as the program stops.
        exec tmux new-session -A -s downloader "START_SH_IN_TMUX=1 $(printf '%q ' "$PWD/start.sh" "$@")"
    fi
    echo "tmux n'est pas installé (ex. sudo apt install tmux) : lancement sans tmux." >&2
fi

VENV_PY=".venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    PY=$(command -v python3 || command -v python)
    if [ -z "$PY" ]; then
        echo "Python 3.8+ introuvable. Installez-le (ex. sudo apt install python3 python3-venv)." >&2
        exit 1
    fi
    echo "Création de l'environnement virtuel .venv..."
    if ! "$PY" -m venv .venv; then
        echo "Impossible de créer .venv (Debian/Ubuntu : sudo apt install python3-venv)." >&2
        exit 1
    fi
fi

# Dependencies (installed into .venv once)
if ! "$VENV_PY" -c "import requests, bs4, tqdm, yt_dlp, curl_cffi, av, Crypto, cloudscraper" 2>/dev/null; then
    echo "Installation des dépendances dans .venv..."
    "$VENV_PY" -m pip install --upgrade pip
    "$VENV_PY" -m pip install -r requirements.txt
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    for pm in "apt-get:sudo apt install ffmpeg" "dnf:sudo dnf install ffmpeg" "pacman:sudo pacman -S ffmpeg" \
              "zypper:sudo zypper install ffmpeg" "brew:brew install ffmpeg"; do
        if command -v "${pm%%:*}" >/dev/null 2>&1; then
            echo "ffmpeg introuvable (fortement conseillé) : ${pm#*:}"
            break
        fi
    done
fi

ARGS=()
[ -n "$CHEMIN_ANIME" ] && ARGS+=(--anime-dir "$CHEMIN_ANIME")
[ -n "$CHEMIN_NBA" ] && ARGS+=(--nba-dir "$CHEMIN_NBA")
[ -n "$CHEMIN_FILMS" ] && ARGS+=(--films-dir "$CHEMIN_FILMS")
if [ -n "$START_SH_IN_TMUX" ]; then
    "$VENV_PY" main.py "${ARGS[@]}" "$@"
    status=$?
    notify_done "$status"
    echo
    read -rp "Programme terminé. Entrée pour fermer la session tmux... " _
    exit "$status"
fi

if [ -n "$NTFY_TOPIC" ]; then
    "$VENV_PY" main.py "${ARGS[@]}" "$@"
    status=$?
    notify_done "$status"
    exit "$status"
fi
exec "$VENV_PY" main.py "${ARGS[@]}" "$@"
