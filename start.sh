#!/usr/bin/env bash
# NBA Replay Downloader - Linux/macOS launcher: runs main.py in a virtual
# environment (.venv), created on first run.
#
#   ./start.sh
#   ./start.sh --url "https://basketball-video.com/..."
#
# Manual equivalent:
#   python3 -m venv .venv && . .venv/bin/activate
#   pip install -r requirements.txt && python main.py

# Default path suggested at the "Chemin" prompt (Enter = this path), e.g.
#   CHEMIN_PAR_DEFAUT="$HOME/Plex/Sports/NBA"
# Empty: the path saved in the program, otherwise ~/Videos/NBA.
CHEMIN_PAR_DEFAUT=""

cd "$(dirname "$0")" || exit 1
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
if ! "$VENV_PY" -c "import requests, bs4, tqdm, yt_dlp, curl_cffi" 2>/dev/null; then
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

if [ -n "$CHEMIN_PAR_DEFAUT" ]; then
    exec "$VENV_PY" main.py --default-dir "$CHEMIN_PAR_DEFAUT" "$@"
fi
exec "$VENV_PY" main.py "$@"
