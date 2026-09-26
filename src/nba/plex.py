"""Plex-friendly file names for NBA replays.

Plex has no sports agent, so games are laid out as a date-based TV show:

    <dossier>/Season 2026/NBA - 2026-06-13 - New York Knicks vs San Antonio Spurs - NBA Finals Game 5.mp4

Point a Plex "TV Shows" library (agent "Personal Media Shows" / "Plex
Series Scanner") at the parent of <dossier> and name <dossier> "NBA".
Parts that aren't merged get " - pt1", " - pt2"... which Plex stacks.
"""
import os
import re

_DATE_WORDS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"


def clean_game_title(title):
    t = title or ""
    t = re.sub(rf"\b{_DATE_WORDS}[\s,\-_./]*\d{{1,2}}(?:st|nd|rd|th)?[\s,\-_./]+\d{{4}}\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(rf"\b\d{{1,2}}(?:st|nd|rd|th)?[\s\-_./]*{_DATE_WORDS}[\s,\-_./]*\d{{4}}\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b\d{1,4}[./-]\d{1,2}[./-]\d{2,4}\b", " ", t)
    t = re.sub(r"\b(full\s+game\s+replay|full\s+match\s+replay|full\s+replay|full\s+game|game\s+replay|replay|watch\s+online|online|free|hd)\b",
               " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip(" -–—|,:")
    # "Knicks vs Spurs NBA Finals Game 5" -> "Knicks vs Spurs - NBA Finals Game 5"
    m = re.match(r"^(.+?\bvs?\.?\s.+?)\s+((?:NBA|WNBA|Game|Playoffs?|Play-In|Finals|Conference|Summer League|Preseason|Cup)\b.*)$", t, re.IGNORECASE)
    if m and " - " not in t:
        t = f"{m.group(1)} - {m.group(2)}"
    return t or "Match"


def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    return name[:180]


def plex_target(game, dest_root):
    """Returns (folder, file stem without extension)."""
    title = clean_game_title(game.title)
    if game.date:
        folder = os.path.join(dest_root, f"Season {game.date.year}")
        stem = f"NBA - {game.date.isoformat()} - {title}"
    else:
        folder = os.path.join(dest_root, "Divers")
        stem = f"NBA - {title}"
    return folder, sanitize_filename(stem)
