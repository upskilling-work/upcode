"""Project rules / context files (AGENTS.md style).

A *rules file* carries project-specific instructions — conventions, build/test
commands, restrictions — that should steer every turn. Upcode discovers them
automatically (no tool call needed) and injects their content into the
orchestrator's and the agents' system prompts.

Discovery, in increasing order of precedence (more local wins / comes last):

1. ``<UPCODE_HOME_DIR>/AGENTS.md`` — a global/shared rules file.
2. From the git root down to the workspace, the first of ``RULES_FILENAMES``
   found in each directory (so a monorepo's root rules come before a package's).
   Outside a git repository, only the workspace directory is inspected.

The recognized file names, in order of preference within a directory, are
``AGENTS.md``, ``UPCODE.md`` and ``CLAUDE.md`` (the de-facto conventions used by
opencode, this project and Claude Code). The first that exists in a directory is
used; the others are ignored there.

``generate_rules_skeleton`` powers the ``/init`` command: it inspects the
repository (stack markers, top-level layout) and returns a ready-to-edit
``AGENTS.md`` skeleton.
"""

from __future__ import annotations

import os
import pathlib

from .agent import home_dir


# Recognized rules file names, in order of preference within a directory.
RULES_FILENAMES: tuple[str, ...] = ("AGENTS.md", "UPCODE.md", "CLAUDE.md")

# Directories ignored when listing the project layout for the skeleton.
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__",
              ".mypy_cache", ".pytest_cache", "dist", "build", ".idea",
              ".upcode"}

# Stack markers -> (language label, suggested build cmd, suggested test cmd).
_STACK_MARKERS: dict[str, tuple[str, str, str]] = {
    "pyproject.toml": ("Python", "pip install -e .", "pytest"),
    "requirements.txt": ("Python", "pip install -r requirements.txt", "pytest"),
    "package.json": ("Node.js / JavaScript", "npm install", "npm test"),
    "go.mod": ("Go", "go build ./...", "go test ./..."),
    "Cargo.toml": ("Rust", "cargo build", "cargo test"),
    "pom.xml": ("Java (Maven)", "mvn compile", "mvn test"),
    "build.gradle": ("Java/Kotlin (Gradle)", "gradle build", "gradle test"),
    "Gemfile": ("Ruby", "bundle install", "bundle exec rspec"),
    "composer.json": ("PHP", "composer install", "vendor/bin/phpunit"),
}


def _rules_in_dir(directory: str) -> str | None:
    """Return the path of the first recognized rules file in ``directory``."""
    for fn in RULES_FILENAMES:
        p = os.path.join(directory, fn)
        if os.path.isfile(p):
            return p
    return None


def _git_root(start: str) -> str | None:
    """Top-level of the git repository containing ``start`` (or ``None``)."""
    d = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def find_rules_files(workspace: str | None = None) -> list[str]:
    """Discover the project's rules files, in increasing order of precedence.

    Returns absolute-or-relative paths (as found), deduplicated, with the most
    global first and the workspace-local one last. ``workspace`` defaults to the
    current directory (which is the workspace; tools resolve from there)."""
    base = os.path.abspath(workspace or os.getcwd())
    paths: list[str] = []

    # 1. Global/shared rules in the Upcode home.
    global_rules = _rules_in_dir(home_dir())
    if global_rules:
        paths.append(global_rules)

    # 2. From the git root down to the workspace (outermost first). Without a
    #    git repo, inspect only the workspace directory.
    root = _git_root(base) or base
    chain: list[str] = []
    d = base
    while True:
        chain.append(d)
        if d == root:
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    chain.reverse()  # outermost (root) first, workspace last
    for directory in chain:
        p = _rules_in_dir(directory)
        if p:
            paths.append(p)

    # Dedupe by absolute path, preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for p in paths:
        key = os.path.abspath(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _display_path(path: str, workspace: str | None) -> str:
    """Path shown in the prompt header — relative to the workspace when possible."""
    base = os.path.abspath(workspace or os.getcwd())
    try:
        rel = os.path.relpath(path, base)
    except ValueError:  # different drive (Windows)
        return path
    return rel if not rel.startswith("..") else os.path.abspath(path)


def rules_prompt(workspace: str | None = None) -> str:
    """System-prompt snippet with the project's rules (or ``""`` if none).

    Concatenates the discovered rules files, each under a header with its path,
    wrapped in an instruction telling the model to follow them."""
    files = find_rules_files(workspace)
    blocks: list[str] = []
    for p in files:
        try:
            text = pathlib.Path(p).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            blocks.append(f"### {_display_path(p, workspace)}\n{text}")
    if not blocks:
        return ""
    return (
        "\n\nPROJECT RULES — instructions from the project's context files "
        "(AGENTS.md / UPCODE.md). Treat them as authoritative for this project; "
        "follow them and prefer them over your defaults when they conflict:\n\n"
        + "\n\n".join(blocks)
    )


# --------------------------------------------------------------------- #
# /init — generate an AGENTS.md skeleton by inspecting the repository
# --------------------------------------------------------------------- #
def _detect_stack(base: str) -> list[tuple[str, str, str]]:
    """Detected stacks as ``(label, build_cmd, test_cmd)`` (deduplicated)."""
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for marker, info in _STACK_MARKERS.items():
        if os.path.isfile(os.path.join(base, marker)) and info[0] not in seen:
            seen.add(info[0])
            found.append(info)
    return found


def _top_level_layout(base: str, limit: int = 24) -> list[str]:
    """Top-level entries of the project (dirs first), skipping noise."""
    try:
        entries = sorted(os.scandir(base), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return []
    out: list[str] = []
    for e in entries:
        if e.name.startswith(".") or e.name in _SKIP_DIRS:
            continue
        out.append(e.name + ("/" if e.is_dir() else ""))
        if len(out) >= limit:
            out.append("…")
            break
    return out


def rules_filename() -> str:
    """Preferred rules file name to create with ``/init`` (``AGENTS.md``)."""
    return RULES_FILENAMES[0]


def generate_rules_skeleton(workspace: str | None = None) -> str:
    """Build an ``AGENTS.md`` skeleton by inspecting the repository.

    Heuristic and deterministic (no LLM): detects the stack from marker files,
    suggests build/test commands and lists the top-level layout. The result is
    meant to be reviewed and filled in by the user."""
    base = os.path.abspath(workspace or os.getcwd())
    name = os.path.basename(base) or "project"
    stacks = _detect_stack(base)
    layout = _top_level_layout(base)

    lines: list[str] = [
        f"# {name}",
        "",
        "> Project rules for AI coding agents (read automatically by Upcode). "
        "Edit freely — keep it short and specific.",
        "",
        "## Overview",
        "",
        "_Describe in one or two sentences what this project does._",
        "",
    ]

    if stacks:
        labels = ", ".join(s[0] for s in stacks)
        lines += ["## Stack", "", f"- Detected: {labels}", ""]
        lines += ["## Build & test", ""]
        for label, build, test in stacks:
            lines.append(f"- {label}: `{build}` to set up, `{test}` to run tests.")
        lines.append("")
    else:
        lines += [
            "## Build & test", "",
            "_No stack markers detected. Document the build, run and test "
            "commands here (e.g. `make build`, `make test`)._", "",
        ]

    if layout:
        lines += ["## Structure", ""]
        lines += [f"- `{entry}`" for entry in layout]
        lines.append("")

    lines += [
        "## Conventions",
        "",
        "- _Code style / formatting rules (e.g. line length, quotes)._",
        "- _Naming conventions._",
        "- _Anything the agent should always or never do._",
        "",
        "## Notes",
        "",
        "- _Gotchas, important context, links to docs._",
        "",
    ]
    return "\n".join(lines)
