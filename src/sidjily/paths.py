"""Emplacements locaux persistants, adaptés aux systèmes Windows et POSIX."""

from __future__ import annotations

import os
from pathlib import Path

APP_DIRECTORY = "SIDJILY"


def user_data_dir() -> Path:
    """Retourne le dossier de données utilisateur sans créer de fichiers dans le dépôt."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_DIRECTORY
