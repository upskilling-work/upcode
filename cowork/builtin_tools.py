"""Example tools ready for use by the Upcode agent."""

from __future__ import annotations

import datetime as _dt
import fnmatch
import html as _html
import os
import pathlib
import re
import subprocess
import urllib.error
import urllib.request
from typing import Callable

from .tools import ToolRegistry, tool


# Confirmation hook for actions that MODIFY the disk (write/delete) or run
# commands. Receives (action, target) and returns True to proceed. If None
# (default), actions happen without confirmation; interactive interfaces set it.
_confirm_hook: Callable[[str, str], bool] | None = None


def set_confirm_hook(hook: Callable[[str, str], bool] | None) -> None:
    """Set (or clear, with ``None``) the change-confirmation function."""
    global _confirm_hook
    _confirm_hook = hook


def _confirm(action: str, path: str) -> bool:
    """Ask the hook whether the action may proceed. Without a hook, it proceeds."""
    if _confirm_hook is None:
        return True
    return bool(_confirm_hook(action, path))


# Change hook for undo/snapshots: called with a file path right BEFORE the file
# is created, edited or deleted, so a snapshot store can capture its prior state.
# None (default) = no recording; the TUI wires it to its SnapshotStore.
_change_hook: Callable[[str], None] | None = None


def set_change_hook(hook: Callable[[str], None] | None) -> None:
    """Set (or clear, with ``None``) the pre-change recording function."""
    global _change_hook
    _change_hook = hook


def _record_change(path: str) -> None:
    """Notify the change hook (if any) that ``path`` is about to be modified."""
    if _change_hook is not None:
        try:
            _change_hook(path)
        except Exception:  # noqa: BLE001 — recording must never break a tool
            pass


# Read-only ("plan") mode: when on, the mutating tools (write/edit/delete/run)
# refuse to act and return an explanatory message. This is a process-wide guard
# so it also covers delegated sub-agents, not just the orchestrator's registry.
_read_only = False


def set_read_only(enabled: bool) -> None:
    """Enable/disable read-only mode for the mutating tools."""
    global _read_only
    _read_only = bool(enabled)


def read_only_enabled() -> bool:
    return _read_only


@tool
def current_time(timezone: str = "local") -> str:
    """Return the current date and time. Use for questions about 'now'/'today'."""
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool
def calculate(expression: str) -> str:
    """Evaluate a simple arithmetic expression (e.g. '2 * (3 + 4)')."""
    allowed = set("0123456789+-*/(). %")
    if not set(expression) <= allowed:
        return "Error: the expression contains disallowed characters."
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))  # noqa: S307 — sanitized above
    except Exception as exc:  # noqa: BLE001
        return f"Calculation error: {exc}"


@tool
def list_files(directory: str = ".") -> str:
    """List the files and folders in a directory."""
    p = pathlib.Path(directory).expanduser()
    if not p.is_dir():
        return f"Error: '{directory}' is not a directory."
    items = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())
    return "\n".join(items) or "(empty)"


@tool
def read_file(path: str, max_chars: int = 4000,
              number_lines: bool = False) -> str:
    """Read the content of a text file (truncated at max_chars).

    To edit code precisely, call with ``number_lines=True``: each line is
    prefixed with its number, which makes it easier to locate the snippet to
    change with `edit_file`.
    """
    p = pathlib.Path(path).expanduser()
    if not p.is_file():
        return f"Error: file '{path}' not found."
    text = p.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    text = text[:max_chars]
    if number_lines:
        lines = text.splitlines()
        width = len(str(len(lines)))
        text = "\n".join(f"{i:>{width}}\t{ln}" for i, ln in enumerate(lines, 1))
    return text + ("\n…(truncated)" if truncated else "")


@tool
def write_file(path: str, content: str) -> str:
    """Create or overwrite a text file with the given content.

    Creates intermediate folders if needed. Use to ACTUALLY create or change
    files — not just to describe the content.
    """
    if _read_only:
        return (f"[plan mode] read-only: '{path}' was NOT written. Propose this "
                "change in your plan instead; the user runs /plan to turn off plan mode and apply it.")
    p = pathlib.Path(path).expanduser()
    action = "modify" if p.exists() else "create"
    if not _confirm(action, str(p)):
        return f"Operation cancelled by the user: '{path}' was not modified."
    _record_change(str(p))  # snapshot prior state for /undo
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error writing '{path}': {exc}"
    lines = content.count("\n") + 1 if content else 0
    return f"File '{path}' saved ({len(content)} chars, {lines} line(s))."


@tool
def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Edit a file by replacing an exact snippet with another (patch-style).

    Preferred over `write_file` for changing existing files: changes only what
    is needed, without rewriting everything. ``old_string`` must appear EXACTLY
    once in the file (including indentation); if it does not appear, or appears
    more than once, the edit is refused — include enough context to make it
    unique.
    """
    if _read_only:
        return (f"[plan mode] read-only: '{path}' was NOT edited. Propose this "
                "change in your plan instead; the user runs /plan to turn off plan mode and apply it.")
    p = pathlib.Path(path).expanduser()
    if not p.is_file():
        return f"Error: file '{path}' not found."
    text = p.read_text(encoding="utf-8", errors="replace")
    occurrences = text.count(old_string)
    if occurrences == 0:
        return ("Error: old_string was not found in the file. "
                "Read the file and copy the exact snippet (with indentation).")
    if occurrences > 1:
        return (f"Error: old_string appears {occurrences} times; it is ambiguous. "
                "Include more lines of context to make it unique.")
    new_text = text.replace(old_string, new_string, 1)
    if not _confirm("edit", str(p)):
        return f"Operation cancelled by the user: '{path}' was not modified."
    _record_change(str(p))  # snapshot prior state for /undo
    try:
        p.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return f"Error writing '{path}': {exc}"
    delta = new_text.count("\n") - text.count("\n")
    return f"File '{path}' edited ({delta:+d} line(s))."


@tool
def delete_file(path: str) -> str:
    """Delete (remove) a file. For safety, it does NOT remove directories.

    Use to ACTUALLY delete a file when the task asks for it.
    """
    if _read_only:
        return (f"[plan mode] read-only: '{path}' was NOT deleted. Propose this "
                "in your plan instead; the user runs /plan to turn off plan mode and apply it.")
    p = pathlib.Path(path).expanduser()
    if not p.exists():
        return f"Error: '{path}' does not exist."
    if p.is_dir():
        return f"Error: '{path}' is a directory; this tool only deletes files."
    if not _confirm("delete", str(p)):
        return f"Operation cancelled by the user: '{path}' was not deleted."
    _record_change(str(p))  # snapshot prior state for /undo
    try:
        p.unlink()
    except OSError as exc:
        return f"Error deleting '{path}': {exc}"
    return f"File '{path}' deleted."


# Directories ignored in code searches.
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__",
             ".mypy_cache", ".pytest_cache", "dist", "build", ".idea"}


@tool
def search_code(pattern: str, directory: str = ".", glob: str = "*",
                max_results: int = 100) -> str:
    """Search a pattern (regex) in the text files under a directory (grep-style).

    Use to locate where something is defined/used in the project without reading
    file by file. ``glob`` filters names (e.g. ``"*.py"``). Returns lines in the
    format ``path:line: content``.
    """
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"Error: invalid regex ({exc})."
    root = pathlib.Path(directory).expanduser()
    if not root.is_dir():
        return f"Error: '{directory}' is not a directory."

    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in sorted(filenames):
            if not fnmatch.fnmatch(name, glob):
                continue
            fp = pathlib.Path(dirpath) / name
            try:
                with fp.open(encoding="utf-8", errors="strict") as fh:
                    for n, line in enumerate(fh, 1):
                        if rx.search(line):
                            rel = os.path.relpath(fp, root)
                            matches.append(f"{rel}:{n}: {line.rstrip()[:200]}")
                            if len(matches) >= max_results:
                                matches.append("…(more results omitted)")
                                return "\n".join(matches)
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable — skip
    return "\n".join(matches) or "(no matches)"


@tool
def run_command(command: str, timeout: int = 60) -> str:
    """Run a shell command in the working directory and return its output.

    Use to run tests, linters, builds, `git`, etc. — whatever a terminal command
    would do. The output (stdout+stderr) is truncated and the exit code is
    reported. Call the tool directly when needed — user approval, when
    applicable, is handled by the interface; do not ask for permission in text.
    """
    if _read_only:
        return (f"[plan mode] read-only: command NOT executed ({command}). "
                "Describe what you would run in your plan; the user runs /plan "
                "to turn off plan mode and execute.")
    if not _confirm("run command", command):
        return f"Operation cancelled by the user: command not executed ({command})."
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=os.getcwd(),
        )
    except subprocess.TimeoutExpired:
        return f"Error: the command exceeded the {timeout}s time limit."
    except OSError as exc:
        return f"Error running the command: {exc}"
    output = (proc.stdout or "") + (proc.stderr or "")
    output = output[:6000] + ("\n…(truncated)" if len(output) > 6000 else "")
    return f"[exit {proc.returncode}]\n{output}".rstrip()


def _html_to_text(html: str) -> str:
    """Extract readable text from HTML (strips scripts, styles and tags)."""
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = _html.unescape(html)
    return re.sub(r"[ \t]*\n\s*\n\s*", "\n\n", re.sub(r"[ \t]+", " ", html)).strip()


@tool
def fetch_url(url: str, max_chars: int = 4000) -> str:
    """Fetch (download) the content of a URL from the internet and return the text.

    Use to obtain data from web pages. HTML pages are converted to text. Call the
    tool directly; user approval, when applicable, is handled by the interface —
    do not ask for permission in text.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not _confirm("fetch from internet", url):
        return f"Operation cancelled by the user: '{url}' was not accessed."
    req = urllib.request.Request(url, headers={"User-Agent": "Upcode/0.2 (+agent)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            ctype = resp.headers.get_content_type()
            charset = resp.headers.get_content_charset() or "utf-8"
            raw = resp.read(2_000_000)  # cap at ~2 MB
    except (urllib.error.URLError, ValueError, OSError) as exc:
        return f"Error fetching '{url}': {exc}"
    text = raw.decode(charset, errors="replace")
    if "html" in ctype:
        text = _html_to_text(text)
    else:
        text = text.strip()
    return text[:max_chars] + ("\n…(truncated)" if len(text) > max_chars else "")


@tool
def update_plan(steps: list[str], current_step: int = 1) -> str:
    """Create or update the TODO plan for the current task and show progress.

    Call at the start of a multi-step task to record the plan, and call again as
    you progress, changing `current_step`. ALWAYS pass the full list of steps.
    Keep each step short (one line).

    Args:
        steps: ordered list of short steps (the whole plan on every call).
        current_step: 1-based index of the step in progress. Steps before it
            count as completed; after it, as pending. Use len(steps)+1 when all
            steps are completed.
    """
    if not steps:
        return "Empty plan."
    lines = ["Plan:"]
    for i, step in enumerate(steps, start=1):
        mark = "✓" if i < current_step else ("→" if i == current_step else "○")
        lines.append(f"  {mark} {i}. {step}")
    if current_step > len(steps):
        lines.append("  ✓ all steps completed")
    return "\n".join(lines)


def default_registry() -> ToolRegistry:
    """Registry with all the example tools."""
    reg = ToolRegistry()
    reg.add(current_time, calculate, list_files, read_file,
            write_file, edit_file, delete_file,
            search_code, run_command, fetch_url, update_plan)
    return reg
