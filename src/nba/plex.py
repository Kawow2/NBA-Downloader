"""Plex-friendly file names for NBA replays.

Plex has no sports agent, so games are laid out as a date-based TV show:

    <dossier>/Season 2026/NBA - 2026-06-13 - New York Knicks vs San Antonio Spurs - NBA Finals Game 5.mp4

(prefixed WNBA / EuroLeague / FIBA / NCAA instead of NBA for those games).

Point a Plex "TV Shows" library (agent "Personal Media Shows" / "Plex
Series Scanner") at the parent of <dossier> and name <dossier> "NBA".
Parts that aren't merged get " - pt1", " - pt2"... which Plex stacks.
"""
import os
import re

_DATE_WORDS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"


def clean_game_title(title):
    t = title or ""
    t = re.sub(rf"\b{_DATE_WORDS}[\s,\-_./]*\d{{1,2}}(?:st|nd|rd|th)?(?:\s*-\s*\d{{1,2}})?[\s,\-_./]+\d{{4}}\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(rf"\b\d{{1,2}}(?:st|nd|rd|th)?[\s\-_./]*{_DATE_WORDS}[\s,\-_./]*\d{{4}}\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b\d{1,4}[./-]\d{1,2}[./-]\d{2,4}\b", " ", t)
    t = re.sub(r"\b(full\s+game\s+replays?|full\s+match\s+replays?|full\s+replays?|full\s+game|game\s+replays?|replays?|watch\s+online|online|free|hd)\b",
               " ", t, flags=re.IGNORECASE)
    # "A vs. B - WNBA - " : the league goes in the file prefix, not the title.
    t = re.sub(r"(\s-\s*)+(W?NBA)\s*(-\s*)*$", "", t.strip(), flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*(-\s*)+", " - ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—|,:")
    # "Knicks vs Spurs NBA Finals Game 5" -> "Knicks vs Spurs - NBA Finals Game 5"
    m = re.match(r"^(.+?\bvs?\.?\s.+?)\s+((?:NBA|WNBA|Game|Playoffs?|Play-In|Finals|Conference|Summer League|Preseason|Cup)\b.*)$", t, re.IGNORECASE)
    if m and " - " not in t:
        t = f"{m.group(1)} - {m.group(2)}"
    return t or "Match"


def league(title):
    for pattern, name in ((r"\bWNBA\b", "WNBA"), (r"\bEuro\s*League\b", "EuroLeague"), (r"\bFIBA\b", "FIBA"),
                          (r"\b(NCAA|College)\b", "NCAA")):
        if re.search(pattern, title or "", re.IGNORECASE):
            return name
    return "NBA"


def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    return name[:180]


def plex_target(game, dest_root, title=None):
    """Returns (folder, file stem without extension). title overrides the
    page title (a page with several games: the chosen game's heading)."""
    title = clean_game_title(title or game.title)
    prefix = league(game.title)
    if game.date:
        folder = os.path.join(dest_root, f"Season {game.date.year}")
        stem = f"{prefix} - {game.date.isoformat()} - {title}"
    else:
        folder = os.path.join(dest_root, "Divers")
        stem = f"{prefix} - {title}"
    return folder, sanitize_filename(stem)
