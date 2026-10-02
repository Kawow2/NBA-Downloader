"""Plex-friendly file layout for films and series.

Movies and series need different Plex libraries, so each goes in its own
subfolder of the chosen Films & Séries folder:

    <dossier>/Films/Titre (2001)/Titre (2001).mp4
    <dossier>/Séries/Ma Série/Season 01/Ma Série - S01E02 - Le titre.mkv

Point a Plex "Films" library at <dossier>/Films and a "Séries TV" library
at <dossier>/Séries.
"""
import os
import re

MOVIES_SUBDIR = "Films"
SERIES_SUBDIR = "Séries"


def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "")
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    return name[:180] or "Sans titre"


def _titled(title, year):
    return f"{title} ({year})" if year else title


def movie_target(dest_root, title, year, ext="mp4"):
    """(folder, filename) for a movie."""
    stem = sanitize_filename(_titled(title, year))
    folder = os.path.join(dest_root, MOVIES_SUBDIR, stem)
    return folder, f"{stem}.{ext}"


def series_episode_target(dest_root, series_title, year, season, number, ep_title="", ext="mp4"):
    """(folder, filename) for one episode of a series."""
    show = sanitize_filename(_titled(series_title, year))
    folder = os.path.join(dest_root, SERIES_SUBDIR, show, f"Season {season:02d}")
    stem = f"{show} - S{season:02d}E{number:02d}"
    ep_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", ep_title or "").strip()
    # Append the episode title only when there is a real one that isn't just
    # the SxxExx marker repeated.
    if ep_title and not re.fullmatch(r"s?\d{1,2}\s*e\s*\d{1,3}", ep_title, re.IGNORECASE):
        stem += f" - {ep_title}"
    return folder, f"{sanitize_filename(stem)}.{ext}"
