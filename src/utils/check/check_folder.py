import os


def folder_problem(path):
    """None if videos can be written to path (created if missing), else
    what's wrong, to show the user before any download starts."""
    if not path:
        return "aucun chemin"
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        return (f"« {path} » n'est pas un chemin complet "
                f"(ex. {os.path.join(os.path.expanduser('~'), 'Videos', 'NBA')} ou /srv/plex/NBA)")
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".nba-downloader-write-test")
        with open(probe, "w"):
            pass
        os.remove(probe)
    except OSError as e:
        hint = ""
        if os.name != "nt" and isinstance(e, PermissionError):
            hint = f" - donnez-le à votre utilisateur (sudo chown -R $USER \"{path}\") ou choisissez-en un autre"
        return f"impossible d'écrire dans {path} ({e.strerror or e}){hint}"
    return None
