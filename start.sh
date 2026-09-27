#!/usr/bin/env bash
# Anime & NBA Downloader - Linux/macOS launcher: runs main.py in a virtual
# environment (.venv), created on first run.
#
#   ./start.sh                  menu: Anime or NBA
#   ./start.sh --tmux           same, inside a tmux session (over SSH: the
#                               downloads keep going if the connection drops;
#                               come back with ./start.sh --tmux again)
#   ./start.sh nba --url "https://basketball-video.com/..."
#
# Manual equivalent:
#   python3 -m venv .venv && . .venv/bin/activate
#   pip install -r requirements.txt && python main.py

# Folders (Plex libraries) for this launcher, e.g.
#   CHEMIN_ANIME="/srv/plex/Anime"   CHEMIN_NBA="/srv/plex/NBA"
# Empty: the folders chosen in the program's menu (Réglages), asked the
# first time each category is used.
CHEMIN_ANIME=""
CHEMIN_NBA=""

cd "$(dirname "$0")" || exit 1

if [ "$1" = "--tmux" ]; then
    shift
    if [ -z "$TMUX" ]; then
        if command -v tmux >/dev/null 2>&1; then
            # -A: reattach to the running session if there is one.
            exec tmux new-session -A -s downloader "$(printf '%q ' "$PWD/start.sh" "$@")"
        fi
        echo "tmux n'est pas installé (ex. sudo apt install tmux) : lancement sans tmux."
    fi
elif [ -n "$SSH_CONNECTION" ] && [ -z "$TMUX" ] && [ -z "$STY" ]; then
    echo "Astuce SSH : ./start.sh --tmux garde les téléchargements actifs si la connexion coupe"
    echo "            (se détacher : Ctrl+B puis D ; revenir : ./start.sh --tmux)."
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
exec "$VENV_PY" main.py "${ARGS[@]}" "$@"
