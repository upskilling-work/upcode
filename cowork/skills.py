"""Agent Skills support.

A *skill* is a reusable capability described in files, discovered in the
``<workspace>/.upcode/skills/`` directory (the workspace configured in
``UPCODE_WORKSPACE``) and in the ``<UPCODE_HOME_DIR>/.skills/`` shared library.
Each skill is a folder with a ``SKILL.md``:

    .upcode/skills/
      review-pr/
        SKILL.md          # frontmatter (name, description) + instructions
        checklist.md      # (optional) extra files of the skill

``SKILL.md`` starts with a YAML frontmatter (Agent Skills standard —
agentskills.io)::

    ---
    name: review-pr
    description: Reviews a pull request following the team's checklist.
    metadata:
      author: team-x
      version: "1.0"
    ---
    # steps
    1. ...

``name`` and ``description`` are required and validated (``name`` format/length,
matching the folder, non-empty ``description``); violations emit warnings without
preventing loading. The optional ``metadata`` map is preserved.

The available skills are listed in the agents' prompt (name + description). When
the task matches a skill, the agent loads the instructions on demand with the
``use_skill`` tool (progressive disclosure).
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
from dataclasses import dataclass, field

import yaml

from .agent import home_dir
from .tools import tool


# Subfolder for the global/shared library (under UPCODE_HOME_DIR).
SKILLS_SUBDIR = ".skills"
# Subfolder for the workspace's own skills, kept under ``.upcode/`` alongside the
# other per-project files (settings.json, mcp.json).
WORKSPACE_SKILLS_SUBDIR = os.path.join(".upcode", "skills")

# Validation limits of the Agent Skills standard (agentskills.io/specification).
_NAME_MAX = 64
_DESC_MAX = 1024
# name: lowercase/digits with single hyphens, no hyphen at the ends nor ``--``.
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class Skill:
    """An Agent Skill discovered in the workspace."""

    name: str
    description: str
    path: str   # absolute path of the SKILL.md
    dir: str    # the skill's folder
    metadata: dict[str, str] = field(default_factory=dict)


def skills_dir(workspace: str | None = None) -> str:
    """Workspace skills folder: ``<workspace>/.upcode/skills`` (default: cwd).

    Upcode's file tools operate relative to the current directory, which is the
    workspace (``apply_workspace``/``/workspace``); that's why the default uses
    the cwd, keeping the skills tied to the project in use."""
    base = workspace or os.getcwd()
    return os.path.join(base, WORKSPACE_SKILLS_SUBDIR)


def skills_dirs(workspace: str | None = None) -> list[str]:
    """Folders to search for skills, in increasing order of precedence.

    1. ``<UPCODE_HOME_DIR>/.skills`` — global/shared library.
    2. ``<workspace>/.upcode/skills`` — the project's local skills.

    The workspace-local ones come last so they take **precedence** on repeated
    names. Duplicate paths are removed."""
    dirs: list[str] = []
    dirs.append(os.path.join(home_dir(), SKILLS_SUBDIR))
    dirs.append(skills_dir(workspace))
    seen: set[str] = set()
    unique: list[str] = []
    for d in dirs:
        key = os.path.abspath(d)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split the frontmatter block (between ``---``) and the Markdown body.

    Returns ``(yaml_text, body)``; without frontmatter, ``("", text)``."""
    if not text.startswith("---"):
        return "", text
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return "", text
    return "\n".join(lines[1:end]), "\n".join(lines[end + 1:]).strip()


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract the YAML frontmatter and the body of a ``SKILL.md``.

    Uses a full YAML parser, so it supports the Agent Skills standard fields,
    including the nested ``metadata`` map and multiline descriptions. Invalid (or
    absent) frontmatter returns ``({}, text)``. The root-level keys are
    normalized to lowercase (the standard's fields are already lowercase)."""
    yaml_text, body = _split_frontmatter(text)
    if not yaml_text.strip():
        return {}, body if text.startswith("---") else text
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return {}, body
    if not isinstance(data, dict):
        return {}, body
    return {str(k).lower(): v for k, v in data.items()}, body


def _coerce_str(value) -> str:
    """Normalize a frontmatter scalar to ``str`` (``None`` -> ``""``)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _validate(name: str, description: str, folder_name: str) -> list[str]:
    """Check ``name``/``description`` against the standard; return warnings (no raise).

    Does not block loading — a malformed skill disappearing silently would be
    worse than loading it with a warning. Covers the agentskills.io rules:
    ``name`` format and length, matching the folder, and non-empty
    ``description``/limit."""
    issues: list[str] = []
    if not name:
        issues.append("'name' missing (using the folder name)")
    else:
        if len(name) > _NAME_MAX:
            issues.append(f"'name' exceeds {_NAME_MAX} characters")
        if not _NAME_RE.match(name):
            issues.append(
                "'name' must contain only lowercase, digits and single hyphens "
                "(no hyphen at start/end nor '--')"
            )
        if name != folder_name:
            issues.append(f"'name' ({name!r}) differs from the folder ({folder_name!r})")
    if not description.strip():
        issues.append("'description' missing or empty")
    elif len(description) > _DESC_MAX:
        issues.append(f"'description' exceeds {_DESC_MAX} characters")
    return issues


def load_skill(skill_md: str) -> Skill:
    """Read a skill's metadata from the path of its ``SKILL.md``.

    Validates ``name``/``description`` against the Agent Skills standard, emitting
    warnings to ``stderr`` without preventing loading."""
    p = pathlib.Path(skill_md)
    folder = p.parent
    text = p.read_text(encoding="utf-8", errors="replace")
    meta, _body = _parse_frontmatter(text)
    name = _coerce_str(meta.get("name")).strip()
    description = _coerce_str(meta.get("description")).strip()
    raw_meta = meta.get("metadata")
    metadata: dict[str, str] = {}
    if isinstance(raw_meta, dict):
        metadata = {str(k): _coerce_str(v) for k, v in raw_meta.items()}
    for issue in _validate(name, description, folder.name):
        print(f"[cowork] warning: skill {folder.name}: {issue}", file=sys.stderr)
    return Skill(
        name=name or folder.name,
        description=description,
        path=str(p),
        dir=str(folder),
        metadata=metadata,
    )


def load_skills(workspace: str | None = None) -> dict[str, Skill]:
    """Discover the skills in ``<UPCODE_HOME_DIR>/.skills`` and ``<workspace>/.upcode/skills``.

    Each skill is a folder with ``SKILL.md``. Returns a dict ``{name: Skill}``;
    on a repeated name, the higher-precedence folder (workspace) wins.
    Nonexistent folders are ignored."""
    out: dict[str, Skill] = {}
    for d in skills_dirs(workspace):
        root = pathlib.Path(d)
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            md = child / "SKILL.md"
            if child.is_dir() and md.is_file():
                try:
                    sk = load_skill(str(md))
                except OSError:
                    continue
                out[sk.name] = sk
    return out


def skills_prompt(workspace: str | None = None) -> str:
    """Snippet for the system prompt listing the available skills (or ``""``)."""
    skills = load_skills(workspace)
    if not skills:
        return ""
    lines = ["", "Available skills (reusable instructions; load with "
             "`use_skill(<name>)` when the task matches the description):"]
    lines += [f"- {s.name}: {s.description}" for s in skills.values()]
    return "\n".join(lines)


# --------------------------------------------------------------------- #
# Tools exposed to the model
# --------------------------------------------------------------------- #
@tool
def list_skills() -> str:
    """List the available Agent Skills (workspace .upcode/skills/ folder).

    Each skill carries reusable instructions; load one of them with `use_skill`.
    """
    skills = load_skills()
    if not skills:
        return "(no skills in .upcode/skills/)"
    return "\n".join(f"- {s.name}: {s.description}" for s in skills.values())


@tool
def use_skill(name: str) -> str:
    """Load an Agent Skill's instructions by name and return its content.

    Use when the task matches a skill's description (see `list_skills`). Follow
    the returned instructions. The skill's extra files can be read with
    `read_file` from the listed paths.
    """
    skills = load_skills()
    skill = skills.get(name)
    if skill is None:
        available = ", ".join(skills) or "(none)"
        return f"Error: skill '{name}' not found. Available: {available}."
    text = pathlib.Path(skill.path).read_text(encoding="utf-8", errors="replace")
    _meta, body = _parse_frontmatter(text)
    # List the skill's extra files (resources/scripts) with absolute paths,
    # which `read_file` opens from any source folder (workspace or external).
    extras: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(skill.dir):
        for fn in sorted(filenames):
            full = os.path.abspath(os.path.join(dirpath, fn))
            if full == os.path.abspath(skill.path):
                continue
            extras.append(full)
    out = f"# Skill: {skill.name}\n\n{body}"
    if extras:
        out += "\n\nFiles in this skill (read with `read_file`):\n"
        out += "\n".join(f"- {e}" for e in extras)
    return out
