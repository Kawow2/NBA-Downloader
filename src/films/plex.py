"""Noms de fichiers « prêts pour Plex » pour les films et les séries.

Sous le dossier choisi, deux sous-dossiers = deux bibliothèques Plex :

    <dossier>/Films/Inception (2010)/Inception (2010).mp4
    <dossier>/Séries/Breaking Bad (2008)/Season 01/Breaking Bad (2008) - S01E03 - Titre.mp4

Pointez une bibliothèque Plex « Films » (agent Films) sur <dossier>/Films
et une bibliothèque « Séries TV » (agent séries) sur <dossier>/Séries.
Plex nomme les épisodes à partir du motif « SxxEyy » : saison et numéro
d'épisode sont donc toujours présents dans le nom de fichier, même quand le
titre de l'épisode est inconnu.
"""
import os
import re

FILMS_SUBDIR = "Films"
SERIES_SUBDIR = "Séries"


def year_of(date_str):
    """Année (YYYY) d'une date type TMDB '2010-07-16', ou None."""
    m = re.search(r"(19|20)\d{2}", str(date_str or ""))
    return m.group(0) if m else None


def sanitize_filename(name):
    """Retire les caractères interdits par les systèmes de fichiers et borne
    la longueur (certains FS plafonnent à 255 octets par composant)."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "")
    name = re.sub(r"\s+", " ", name).strip().rstrip(" .")
    return name[:180] or "Sans titre"


def _titled(title, year):
    return f"{title} ({year})" if year else (title or "Sans titre")


def movie_target(dest_root, title, year=None):
    """(dossier, nom de fichier sans extension) pour un film."""
    name = sanitize_filename(_titled(title, year))
    folder = os.path.join(dest_root, FILMS_SUBDIR, name)
    return folder, name


def series_target(dest_root, show, season, episode, year=None, episode_title=None):
    """(dossier, nom de fichier sans extension) pour un épisode de série."""
    show_name = sanitize_filename(_titled(show, year))
    season = int(season)
    episode = int(episode)
    folder = os.path.join(dest_root, SERIES_SUBDIR, show_name, f"Season {season:02d}")
    stem = f"{show_name} - S{season:02d}E{episode:02d}"
    if episode_title:
        stem = f"{stem} - {episode_title}"
    return folder, sanitize_filename(stem)
