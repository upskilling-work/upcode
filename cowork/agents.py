"""Agents defined in Markdown — in the Claude Code style.

Each agent is a ``.md`` file inside a ``.agents/`` directory, with a YAML
frontmatter and the body serving as the *system prompt*::

    ---
    name: programmer
    description: Writes, explains and reviews code; navigates and edits the project.
    tools: read_file, write_file, edit_file, search_code, run_command   # optional
    model: claude-sonnet                                                # optional
    ---
    You are a senior software engineer. Write correct and lean code...

Frontmatter fields:

* ``name`` (required): short identifier used in delegation.
* ``description`` (required): the orchestrator reads this to decide when to
  invoke the agent.
* ``tools`` (optional): comma-separated list of the allowed tool names. Omitted
  = the default set (files + search + internet + skills); ``all`` or ``*`` = all
  tools. Accepts the project names (``read_file``, ``run_command``, …) and
  Claude Code-style aliases (``Read``, ``Bash``, ``Grep``, …).
* ``model`` (optional): overrides the orchestrator's model for this agent.

The files are searched in ``<UPCODE_HOME_DIR>/.agents`` (global library) and in
``<workspace>/.agents`` (the project's ones, which take precedence). Files
starting with ``_`` are ignored (e.g. ``_template.md``).
"""

from __future__ import annotations

import glob
import os
import pathlib
import sys

from .agent import home_dir
from .manager import Agent, AgentRegistry
from .tools import Tool, ToolRegistry
from .builtin_tools import (
    calculate,
    current_time,
    delete_file,
    edit_file,
    fetch_url,
    list_files,
    read_file,
    run_command,
    search_code,
    update_plan,
    write_file,
)
from .skills import (
    _coerce_str,
    _parse_frontmatter,
    list_skills,
    skills_prompt,
    use_skill,
)
from .rules import rules_prompt


# Tools available to ALL agents: files
# (list/read/write/edit/delete) + code search + internet fetch +
# Agent Skills (.skills/).
_FILE_TOOLS: tuple[Tool, ...] = (
    list_files, read_file, write_file, edit_file,
    delete_file, search_code, fetch_url,
    list_skills, use_skill,
)

# Reminder appended to each agent's prompt so that it ACTS, not just describes.
_ACT_REMINDER = (
    " When the task asks you to create, save, change or delete files, USE the "
    "tools (`write_file`, `edit_file`, `delete_file`) to actually do it — do not "
    "merely describe the content or say what should be done. If a tool reports "
    "that the operation was CANCELLED by the user, report that honestly — do NOT "
    "say it was completed."
)


def make_agent(name: str, description: str, system_prompt: str,
                    extra_tools: list[Tool] | tuple[Tool, ...] = (),
                    tools: list[Tool] | tuple[Tool, ...] | None = None,
                    model: str | None = None) -> Agent:
    """Create an :class:`Agent` with the "act" reminder and the skills in the prompt.

    By default the agent gets the default tools (``_FILE_TOOLS``) plus
    ``extra_tools``. If ``tools`` is provided, it replaces that set (an explicit
    list of tools). ``model`` overrides the model inherited from the
    orchestrator.
    """
    reg = ToolRegistry()
    if tools is None:
        reg.add(*_FILE_TOOLS, *extra_tools)
    else:
        reg.add(*tools)
    return Agent(
        name=name,
        description=description,
        # The workspace skills are listed in the prompt (progressive disclosure):
        # the agent loads a skill's instructions with `use_skill` on demand.
        # The project's rules (AGENTS.md/UPCODE.md) are appended too, so every
        # agent follows the project's conventions.
        system_prompt=system_prompt.rstrip() + _ACT_REMINDER + skills_prompt()
        + rules_prompt(),
        tools=reg,
        model=model,
    )


# --------------------------------------------------------------------- #
# Tool catalog (for the frontmatter `tools` field)
# --------------------------------------------------------------------- #
# Claude Code-style aliases -> the tool's real name in the project.
_TOOL_ALIASES: dict[str, str] = {
    "read": "read_file",
    "write": "write_file",
    "edit": "edit_file",
    "delete": "delete_file",
    "bash": "run_command",
    "shell": "run_command",
    "grep": "search_code",
    "glob": "list_files",
    "ls": "list_files",
    "webfetch": "fetch_url",
    "fetch": "fetch_url",
    "browser": "browser_test",
}


def _tool_catalog() -> dict[str, Tool]:
    """All the tools an agent can request in the ``tools`` field."""
    catalog: dict[str, Tool] = {
        t.name: t for t in (
            list_files, read_file, write_file, edit_file, delete_file,
            search_code, run_command, fetch_url, calculate, current_time,
            update_plan, list_skills, use_skill,
        )
    }
    # browser_test depends on playwright_tools (cheap import; playwright is lazy).
    try:
        from .playwright_tools import browser_test
        catalog[browser_test.name] = browser_test
    except Exception:  # noqa: BLE001 — without playwright_tools, continue without browser_test
        pass
    return catalog


def _resolve_tools(spec: str, agent_name: str) -> list[Tool] | None:
    """Resolve the ``tools`` field string into a list of :class:`Tool`.

    Returns ``None`` when the field is absent/empty (= uses the default set).
    ``all``/``*`` returns all tools. Unknown names emit a warning.
    """
    spec = (spec or "").strip()
    if not spec:
        return None
    catalog = _tool_catalog()
    if spec.lower() in ("all", "*", "todas"):
        return list(catalog.values())
    resolved: list[Tool] = []
    seen: set[str] = set()
    for raw in spec.replace("\n", ",").split(","):
        token = raw.strip()
        if not token:
            continue
        name = _TOOL_ALIASES.get(token.lower(), token)
        tool_obj = catalog.get(name)
        if tool_obj is None:
            print(f"[cowork] warning: agent {agent_name}: unknown tool "
                  f"{token!r} (ignored).", file=sys.stderr)
            continue
        if tool_obj.name not in seen:
            seen.add(tool_obj.name)
            resolved.append(tool_obj)
    return resolved or None


# --------------------------------------------------------------------- #
# Loading the Markdown agents (.agents/*.md)
# --------------------------------------------------------------------- #
AGENTS_SUBDIR = ".agents"


def agents_dirs(workspace: str | None = None) -> list[str]:
    """Folders to search for agents, in increasing order of precedence.

    1. ``<UPCODE_HOME_DIR>/.agents`` — global/shared library.
    2. ``<workspace>/.agents`` — the project's local agents (precedence).

    Duplicate paths are removed."""
    base = workspace or os.getcwd()
    dirs = [
        os.path.join(home_dir(), AGENTS_SUBDIR),
        os.path.join(base, AGENTS_SUBDIR),
    ]
    seen: set[str] = set()
    unique: list[str] = []
    for d in dirs:
        key = os.path.abspath(d)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def load_agent_md(path: str) -> Agent | None:
    """Load an agent from a ``.md`` file with frontmatter.

    Returns ``None`` (with a warning) if ``name`` or ``description`` is missing."""
    p = pathlib.Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter(text)
    name = _coerce_str(meta.get("name")).strip() or p.stem
    description = _coerce_str(meta.get("description")).strip()
    model = _coerce_str(meta.get("model")).strip() or None
    tools = _resolve_tools(_coerce_str(meta.get("tools")), name)

    if not description:
        print(f"[cowork] warning: agent {p.name}: 'description' missing — "
              "skipped.", file=sys.stderr)
        return None
    if not body.strip():
        print(f"[cowork] warning: agent {p.name}: body (system prompt) empty "
              "— skipped.", file=sys.stderr)
        return None

    return make_agent(
        name=name,
        description=description,
        system_prompt=body,
        tools=tools,
        model=model,
    )


def load_agents(workspace: str | None = None) -> AgentRegistry:
    """Discover and load the ``.md`` agents from ``.agents/`` (home + workspace).

    Files starting with ``_`` are ignored. Errors in one file don't bring down
    the others. Repeated names: the higher-precedence folder (workspace) wins.
    """
    reg = AgentRegistry()
    for folder in agents_dirs(workspace):
        if not os.path.isdir(folder):
            continue
        for path in sorted(glob.glob(os.path.join(folder, "*.md"))):
            if os.path.basename(path).startswith("_"):
                continue
            try:
                agent = load_agent_md(path)
            except Exception as exc:  # noqa: BLE001 — a bad file doesn't break the rest
                print(f"[cowork] error loading {path}: {exc}", file=sys.stderr)
                continue
            if agent is not None:
                reg.add(agent)
    return reg


def default_agents(workspace: str | None = None) -> AgentRegistry:
    """Team of agents loaded from the ``.md`` files in ``.agents/``.

    ``workspace`` defines where to look for the local ``.agents/`` (besides the
    global one in ``UPCODE_HOME_DIR``); ``None`` uses the current directory."""
    return load_agents(workspace)
