"""Persistent sessions: save and resume conversations.

A *session* is the orchestrator's full message history plus metadata (model,
workspace, timestamps and a title derived from the first prompt), stored as
``<workspace>/.upcode/sessions/<id>.json``. This mirrors the persistence style
of :mod:`cowork.settings`: per-workspace and best-effort (write failures never
break the session).

The history is kept in the canonical OpenAI-chat shape — the same list the
agent holds in memory — so resuming is just assigning it back. Vendor-specific
blocks (``_anthropic_blocks`` / ``_gemini_parts``) are plain dicts and survive
the JSON round-trip, preserving thinking/tool continuity across a resume.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any

# Subfolder (relative to the workspace) where session files live.
SESSIONS_SUBDIR = os.path.join(".upcode", "sessions")


def sessions_dir(workspace: str) -> str:
    """Directory holding the workspace's session files."""
    return os.path.join(workspace, SESSIONS_SUBDIR)


def session_path(workspace: str, session_id: str) -> str:
    """Path of a single session file."""
    return os.path.join(sessions_dir(workspace), session_id + ".json")


def new_session_id() -> str:
    """A fresh, sortable session id based on the current time."""
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _title_from(messages: list[dict]) -> str:
    """Short title from the first non-empty user message (for listings)."""
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            text = " ".join(m["content"].split())
            if text:
                return text[:60]
    return "(no prompt yet)"


def has_content(messages: list[dict]) -> bool:
    """True if the history has more than the bare system prompt (worth saving)."""
    return any(m.get("role") in ("user", "assistant") for m in messages)


def save_session(workspace: str, session_id: str, messages: list[dict],
                 *, model: str = "", extra: dict[str, Any] | None = None
                 ) -> str | None:
    """Persist ``messages`` + metadata to the session file. Returns the path.

    Preserves the original ``created`` timestamp when updating an existing file.
    Returns ``None`` on any write/serialization failure (best-effort)."""
    path = session_path(workspace, session_id)
    now = _dt.datetime.now().isoformat(timespec="seconds")
    created = now
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                created = json.load(fh).get("created", now)
        except (OSError, json.JSONDecodeError):
            pass
    data: dict[str, Any] = {
        "id": session_id,
        "created": created,
        "updated": now,
        "model": model,
        "workspace": workspace,
        "title": _title_from(messages),
        "messages": messages,
    }
    if extra:
        data.update(extra)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except (OSError, TypeError, ValueError):
        return None
    return path


def list_sessions(workspace: str) -> list[dict]:
    """All saved sessions for the workspace, most recently updated first."""
    directory = sessions_dir(workspace)
    if not os.path.isdir(directory):
        return []
    out: list[dict] = []
    for name in os.listdir(directory):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("id"):
            out.append(data)
    out.sort(key=lambda s: s.get("updated", ""), reverse=True)
    return out


def load_session(workspace: str, session_id: str) -> dict | None:
    """Load a session by id. Returns ``None`` if missing or malformed."""
    path = session_path(workspace, session_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        return data
    return None
