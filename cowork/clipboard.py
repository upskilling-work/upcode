"""Read an image from the OS clipboard.

Terminals deliver *paste* as text (bracketed paste), so image bytes never reach
the TUI that way. To attach a screenshot we have to talk to the OS clipboard
directly. :func:`grab_image` does that per platform and returns a neutral image
payload — ``(media_type, base64_data)`` — ready to drop into a multimodal user
message (see :mod:`cowork.providers`). It returns ``None`` when the clipboard
holds no image (or the platform tool is unavailable), so callers can fall back
to a normal text paste.

macOS uses ``pngpaste`` when installed (fast) and otherwise ``osascript`` (no
extra dependency). Linux uses ``wl-paste``/``xclip``; Windows uses PowerShell.
"""

from __future__ import annotations

import base64
import os
import platform
import subprocess
import tempfile

_TIMEOUT = 5


def grab_image() -> tuple[str, str] | None:
    """Return ``(media_type, base64_data)`` for a clipboard image, or ``None``."""
    system = platform.system()
    if system == "Darwin":
        return _macos()
    if system == "Linux":
        return _linux()
    if system == "Windows":
        return _windows()
    return None


def grab_text() -> str | None:
    """Return the clipboard's text, or ``None`` if empty/unavailable.

    Used for the manual Ctrl+V fallback on Linux/Windows (macOS pastes through
    the terminal natively)."""
    system = platform.system()
    if system == "Darwin":
        r = _run(["pbpaste"])
    elif system == "Linux":
        r = _run(["wl-paste", "--no-newline"]) or None
        if r is None or r.returncode != 0:
            r = _run(["xclip", "-selection", "clipboard", "-o"])
    elif system == "Windows":
        r = _run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"])
    else:
        return None
    if r is not None and r.returncode == 0 and r.stdout:
        return r.stdout.decode("utf-8", "replace")
    return None


def _encode(data: bytes | None, media_type: str) -> tuple[str, str] | None:
    if not data:
        return None
    return media_type, base64.b64encode(data).decode("ascii")


def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None


# --------------------------------------------------------------------- #
# macOS
# --------------------------------------------------------------------- #
def _macos() -> tuple[str, str] | None:
    # Fast path: pngpaste streams the clipboard image (PNG) to stdout.
    r = _run(["pngpaste", "-"])
    if r is not None and r.returncode == 0 and r.stdout:
        return _encode(r.stdout, "image/png")

    # Fallback (no dependency): AppleScript coerces the clipboard to PNG and
    # writes it to a temp file. Fails (non-zero) when there is no image.
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    script = (
        'set thePng to (the clipboard as «class PNGf»)\n'
        f'set theFile to open for access POSIX file "{path}" with write permission\n'
        'set eof theFile to 0\n'
        'write thePng to theFile\n'
        'close access theFile'
    )
    try:
        r = _run(["osascript", "-e", script])
        if r is not None and r.returncode == 0 and os.path.getsize(path) > 0:
            with open(path, "rb") as fh:
                return _encode(fh.read(), "image/png")
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# --------------------------------------------------------------------- #
# Linux
# --------------------------------------------------------------------- #
def _linux() -> tuple[str, str] | None:
    # Wayland first, then X11. Both can emit PNG bytes straight to stdout.
    r = _run(["wl-paste", "-t", "image/png"])
    if r is not None and r.returncode == 0 and r.stdout:
        return _encode(r.stdout, "image/png")
    r = _run(["xclip", "-selection", "clipboard", "-t", "image/png", "-o"])
    if r is not None and r.returncode == 0 and r.stdout:
        return _encode(r.stdout, "image/png")
    return None


# --------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------- #
def _windows() -> tuple[str, str] | None:
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    # Save the clipboard image to a PNG via .NET, if there is one.
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$img = [System.Windows.Forms.Clipboard]::GetImage();"
        f"if ($img -ne $null) {{ $img.Save('{path}'); }} else {{ exit 1 }}"
    )
    try:
        r = _run(["powershell", "-NoProfile", "-Command", ps])
        if r is not None and r.returncode == 0 and os.path.getsize(path) > 0:
            with open(path, "rb") as fh:
                return _encode(fh.read(), "image/png")
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
