"""Per-workspace persistent settings.

Stored as ``<workspace>/.upcode/settings.json``. Used to remember UI toggles
like auto-approval and parallel agent execution across sessions. The settings
are tied to the workspace, so each project keeps its own preferences.
"""

from __future__ import annotations

import json
import os

# Subfolder (relative to the workspace) and file name for the settings.
SETTINGS_SUBDIR = ".upcode"
SETTINGS_FILE = "settings.json"


def settings_path(workspace: str) -> str:
    """Path of the settings file for ``workspace``."""
    return os.path.join(workspace, SETTINGS_SUBDIR, SETTINGS_FILE)


def load_settings(workspace: str) -> dict:
    """Load the workspace settings. Returns ``{}`` if missing or invalid."""
    path = settings_path(workspace)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(workspace: str, data: dict) -> None:
    """Persist ``data`` (best-effort) to ``<workspace>/.upcode/settings.json``.

    Write failures are ignored so they never break the session.
    """
    path = settings_path(workspace)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError:
        pass
