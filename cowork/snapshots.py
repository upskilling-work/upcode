"""In-session file snapshots for undo.

Before the agent modifies a file (``write_file``/``edit_file``/``delete_file``),
the file's original bytes are captured into the current *checkpoint* — one
checkpoint per turn. ``/undo`` restores the most recent checkpoint, reverting
the agent's file changes (rewriting changed files, deleting files it created,
recreating files it deleted).

Scope and limits (intentional, for safety):

- **In-memory / session-only.** Checkpoints live for the running Upcode session;
  they are not persisted across restarts.
- **Files only.** It reverts file edits, not the conversation (use ``/reset``).
- **Precise.** It only ever touches paths the agent actually wrote to, so it
  cannot clobber unrelated work — unlike a blanket ``git restore``.
"""

from __future__ import annotations

import os
import threading


class SnapshotStore:
    """A stack of file checkpoints with single-step :meth:`undo`."""

    def __init__(self) -> None:
        # Finalized checkpoints (oldest first). Each: {"label", "files": {path: bytes|None}}.
        # A value of ``None`` means the file did not exist when first recorded.
        self._checkpoints: list[dict] = []
        self._active: dict | None = None
        self._lock = threading.Lock()

    def begin(self, label: str = "") -> None:
        """Open a new (empty) checkpoint for the turn about to run."""
        with self._lock:
            self._active = {"label": label, "files": {}}

    def record(self, path: str) -> None:
        """Capture a path's original bytes before it is modified (once per path).

        Safe to call from multiple agent threads during a parallel turn — they
        all record into the same active checkpoint."""
        with self._lock:
            if self._active is None:
                self._active = {"label": "", "files": {}}
            ap = os.path.abspath(os.path.expanduser(path))
            if ap in self._active["files"]:
                return  # keep the earliest state seen this turn
            try:
                if os.path.isfile(ap):
                    with open(ap, "rb") as fh:
                        self._active["files"][ap] = fh.read()
                else:
                    self._active["files"][ap] = None  # did not exist yet
            except OSError:
                pass

    def commit(self) -> bool:
        """Finalize the active checkpoint. Returns True if it captured anything."""
        with self._lock:
            cp, self._active = self._active, None
            if cp and cp["files"]:
                self._checkpoints.append(cp)
                return True
            return False

    def can_undo(self) -> bool:
        return bool(self._checkpoints)

    def undo(self) -> dict | None:
        """Restore the most recent checkpoint. Returns a summary, or ``None``.

        Summary: ``{"label", "restored": [paths], "failed": [paths]}``."""
        with self._lock:
            if not self._checkpoints:
                return None
            cp = self._checkpoints.pop()
        restored: list[str] = []
        failed: list[str] = []
        for ap, original in cp["files"].items():
            try:
                if original is None:
                    if os.path.exists(ap):
                        os.remove(ap)
                else:
                    os.makedirs(os.path.dirname(ap), exist_ok=True)
                    with open(ap, "wb") as fh:
                        fh.write(original)
                restored.append(ap)
            except OSError:
                failed.append(ap)
        return {"label": cp["label"], "restored": restored, "failed": failed}
