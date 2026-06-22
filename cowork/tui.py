"""Full-screen Upcode TUI, in the Codex (OpenAI) style.

Terminal interface with a scrollable conversation area, a bordered *composer*,
live streaming from the orchestrator and the agents, a work indicator and
**Esc to interrupt**.

Run with:
    python -m cowork.tui
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import threading
import time

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from .agent import AgentConfig, Event, apply_workspace
from .builtin_tools import set_change_hook, set_confirm_hook, set_read_only
from .models import (
    ModelProfile,
    load_models,
    needs_api_key,
    resolve_last_profile,
    save_last_config,
    thinking_budget_for,
)
from .manager import Orchestrator
from .skills import load_skills
from .agents import default_agents
from .rules import (
    find_rules_files,
    generate_rules_skeleton,
    rules_filename,
)
from .playwright_tools import headless_enabled, set_headless
from .settings import load_settings, save_settings
from .session import (
    has_content,
    list_sessions,
    load_session,
    new_session_id,
    save_session,
)
from .snapshots import SnapshotStore


# Commands offered in the bar menu (when typing "/").
COMMANDS: list[tuple[str, str]] = [
    ("/model", "choose the configured model (LLM)"),
    ("/compact", "summarize the history to free up context"),
    ("/workspace", "show or change the working directory"),
    ("/status", "model, endpoint and context"),
    ("/agents", "list the available agents"),
    ("/skills", "list the available Agent Skills"),
    ("/mcp", "list connected MCP servers and their tools"),
    ("/rules", "show the project rules in effect (AGENTS.md)"),
    ("/init", "generate an AGENTS.md skeleton for this project"),
    ("/diff", "show the working tree's git diff"),
    ("/undo", "revert the file changes from the last turn"),
    ("/sessions", "list saved sessions in this workspace"),
    ("/resume", "resume a saved session (/resume <id>)"),
    ("/plan", "toggle read-only plan mode (no edits)"),
    ("/auto", "toggle auto-approval of edits and commands"),
    ("/parallel", "toggle parallel (concurrent) agent execution"),
    ("/headless", "toggle headless (no window) browser tests"),
    ("/reset", "clear the conversation context"),
    ("/clear", "clear the screen (keeps the conversation)"),
    ("/help", "help and keyboard shortcuts"),
    ("/quit", "close Upcode (also: /exit)"),
]

# Commands that take an argument: when selected in the menu, they fill the field
# (with a space) instead of running immediately.
ARG_COMMANDS: set[str] = {"/workspace", "/model", "/resume"}

# Aliases: share the SAME line in the menu as the canonical command, but also
# make the entry appear when typing the alias prefix (e.g. "/e" → /quit).
COMMAND_ALIASES: dict[str, str] = {"/exit": "/quit"}


# Spinner frames (braille). The work label reflects the agent's REAL stage
# (derived from events), not a timed cycle — see `_stage_for_tool`.
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Label (verb) shown while the agent only thinks/responds, without a tool.
_STAGE_THINKING = "Thinking"

# Maps the name of the running tool to a stage label. Tools not in the map
# fall back to "Working".
_TOOL_STAGES: dict[str, str] = {
    "read_file": "Reading",
    "list_files": "Analyzing",
    "search_code": "Searching",
    "write_file": "Writing",
    "edit_file": "Editing",
    "delete_file": "Editing",
    "run_command": "Running",
    "fetch_url": "Fetching",
    "update_plan": "Planning",
    "use_skill": "Using skill",
    "delegate": "Coordinating",
    "calculate": "Calculating",
    "current_time": "Looking up",
}


def _stage_for_tool(name: str) -> str:
    """Stage label for the tool ``name`` (default: "Working")."""
    return _TOOL_STAGES.get(name, "Working")


def _shimmer(word: str, phase: int) -> Text:
    """Render ``word`` with a glow sweeping across the letters (Codex effect)."""
    t = Text()
    pos = phase % (len(word) + 6)  # sweep and pause outside the word
    for i, ch in enumerate(word):
        d = abs(i - pos)
        if d == 0:
            style = "bold bright_white"
        elif d == 1:
            style = "white"
        elif d == 2:
            style = "grey70"
        elif d == 3:
            style = "grey53"
        else:
            style = "grey37"
        t.append(ch, style=style)
    return t


# Pasting accepts text or *objects*. An object is a copied image or a file path
# (image/video/audio/document/other), shown in the composer as an atomic marker
# like ``[Image 1]`` / ``[Document 2]``.
_IMAGE_EXTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
_DOC_EXTS = {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".csv", ".xlsx",
             ".xls", ".pptx", ".ppt", ".json", ".yaml", ".yml"}
_MARKER_RE = re.compile(r"\[(Image|Video|Audio|Document|File) (\d+)\]")


def _classify_kind(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in _IMAGE_EXTS:
        return "Image"
    if ext in _VIDEO_EXTS:
        return "Video"
    if ext in _AUDIO_EXTS:
        return "Audio"
    if ext in _DOC_EXTS:
        return "Document"
    return "File"


def _load_image_block(path: str) -> dict | None:
    """Read an image file into a neutral image block (``None`` if unreadable)."""
    media_type = _IMAGE_EXTS.get(os.path.splitext(path)[1].lower())
    if not media_type:
        return None
    try:
        with open(path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return None
    return {"type": "image", "media_type": media_type, "data": data}


class CommandInput(Input):
    """Composer input.

    - While the command menu is open, arrows/Enter/Esc drive the menu.
    - Paste (Cmd+V on macOS, Ctrl+V on Windows/Linux) accepts text *or* objects:
      a copied image, or pasted file path(s), become atomic ``[Kind N]`` markers.
    - Backspace/Delete touching a marker removes the whole marker (and object)."""

    # Ctrl+V reads the OS clipboard ourselves (image/file → marker, else text).
    # This is the reliable image-paste path on every platform: terminals don't
    # forward Cmd+V, and an image-only clipboard never produces a paste event —
    # so the native Cmd+V (handled by _on_paste) only ever carries text.
    BINDINGS = [
        Binding("ctrl+v", "obj_paste", "Paste", show=False),
    ]

    def on_key(self, event: events.Key) -> None:
        app = self.app
        if getattr(app, "menu_open", False) and event.key in (
            "down", "up", "enter", "tab", "escape",
        ):
            app.handle_menu_key(event.key)
            event.prevent_default()
            event.stop()
            return
        if event.key in ("backspace", "delete") and self._delete_marker(
                forward=event.key == "delete"):
            event.prevent_default()
            event.stop()

    def _delete_marker(self, *, forward: bool) -> bool:
        """If the edit point is inside a ``[Kind N]`` marker, remove it whole."""
        text = self.value
        # Backspace acts on the char before the cursor; Delete on the char under it.
        target = self.cursor_position if forward else self.cursor_position - 1
        for m in _MARKER_RE.finditer(text):
            if m.start() <= target < m.end():
                self.value = text[:m.start()] + text[m.end():]
                self.cursor_position = m.start()
                self.app._drop_object(int(m.group(2)))
                return True
        return False

    def _on_paste(self, event: events.Paste) -> None:
        """Native terminal paste (Cmd+V on macOS; bracketed paste elsewhere).

        ``prevent_default`` stops ``Input._on_paste`` (next in the MRO) from also
        inserting the text — we do all insertion ourselves in ``_handle_paste``."""
        self.app._handle_paste(self, event.text)
        event.prevent_default()
        event.stop()

    def action_obj_paste(self) -> None:
        """Ctrl+V: read the OS clipboard ourselves (image/file marker, else text)."""
        self.app._handle_paste(self, None)


# --------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------- #
def _fmt_args(arguments: str) -> str:
    try:
        data = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return (arguments or "").strip()
    return ", ".join(f"{k}={v!r}" for k, v in data.items())


def _short(text: str, limit: int = 84) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _render_diff(text: str, max_lines: int = 40) -> Text:
    """Colorize a unified diff (+ green, - red, @@ cyan), truncating."""
    out = Text()
    lines = (text or "").splitlines()
    for line in lines[:max_lines]:
        if line.startswith(("+++", "---")):
            style = "dim"
        elif line.startswith("+"):
            style = "green"
        elif line.startswith("-"):
            style = "red"
        elif line.startswith("@@"):
            style = "cyan"
        else:
            style = "dim"
        out.append(line + "\n", style=style)
    if len(lines) > max_lines:
        out.append(f"… (+{len(lines) - max_lines} line(s))\n", style="dim")
    return out


def _human(n: int | None) -> str:
    """123456 -> '123K', 1000000 -> '1M' (for context/output window)."""
    if not n:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n // 1000}K"
    return str(n)


# --------------------------------------------------------------------- #
# Builder for a turn's transcript (accumulated Rich Text)
# --------------------------------------------------------------------- #
class TurnBuilder:
    """Accumulate what happens in a turn as a styled ``rich.text.Text``.

    Mirrors the CLI renderer: `•` for delegations, `└` for tools, dimmed text
    for the agents' "thinking" and normal text for the orchestrator's synthesis.
    """

    def __init__(self) -> None:
        self.text = Text()
        self._delegated_streaming = False

    def _newline_if_needed(self) -> None:
        if self.text.plain and not self.text.plain.endswith("\n"):
            self.text.append("\n")

    def orchestrator_text(self, chunk: str) -> None:
        if self._delegated_streaming:
            self._newline_if_needed()
            self._delegated_streaming = False
        self.text.append(chunk)

    def delegate(self, agent_name: str, task: str) -> None:
        self._newline_if_needed()
        self._delegated_streaming = False
        self.text.append("• ", style="cyan")
        self.text.append(agent_name, style="bold")
        self.text.append(f"  {_short(task, 72)}\n", style="dim")

    def delegated_event(self, agent_name: str, ev: Event) -> None:
        if ev.kind == "text":
            if not self._delegated_streaming:
                self.text.append("  ")
                self._delegated_streaming = True
            self.text.append(ev.text, style="dim")
        elif ev.kind == "tool_call":
            self._newline_if_needed()
            self._delegated_streaming = False
            self.text.append("  └ ", style="dim")
            self.text.append(ev.name, style="green")
            self.text.append(f"({_short(_fmt_args(ev.arguments), 60)})\n", style="dim")
        elif ev.kind == "tool_result":
            self.text.append(f"    {_short(ev.result, 84)}\n", style="dim")

    def agent_event(self, ev: Event) -> None:
        """Render an event from the agent itself (text + tool usage)."""
        if ev.kind == "text":
            self.orchestrator_text(ev.text)
            return
        # The delegation is already shown by delegate()/delegated_event(); no duplicate.
        if ev.name == "delegate":
            return
        if ev.kind == "tool_call":
            self._newline_if_needed()
            self._delegated_streaming = False
            self.text.append("⚙ ", style="yellow")
            self.text.append(ev.name, style="green")
            self.text.append(f"({_short(_fmt_args(ev.arguments), 60)})\n", style="dim")
        elif ev.kind == "tool_result":
            # Plan and skills appear in full (multiline); everything else, summarized.
            if ev.name in ("update_plan", "use_skill"):
                for line in ev.result.splitlines():
                    self.text.append(f"  {line}\n", style="dim")
            else:
                self.text.append(f"  {_short(ev.result, 84)}\n", style="dim")


# --------------------------------------------------------------------- #
# File-change confirmation modal
# --------------------------------------------------------------------- #
class ConfirmScreen(ModalScreen[str]):
    """Ask whether a file change may proceed.

    Shows a numbered list: click an option, navigate with ↑/↓ + Enter, or type
    the number (1/2/3). Returns (via ``dismiss``) ``"yes"``, ``"always"`` or
    ``"no"``.
    """

    # Options in the order they appear (number = position).
    OPTIONS = [("yes", "Yes"), ("always", "Always (don't ask again)"),
               ("no", "No")]

    BINDINGS = [
        Binding("1", "choose(0)", "Yes"),
        Binding("2", "choose(1)", "Always"),
        Binding("3", "choose(2)", "No"),
        Binding("escape", "cancel", "No"),
    ]

    def __init__(self, action: str, path: str) -> None:
        super().__init__()
        self.action = action
        self.path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(
                Text.assemble(
                    ("⚠ confirm: ", "bold yellow"),
                    (self.action + "\n", "bold yellow"),
                    (self.path, "white"),
                ),
                id="confirm-text",
            )
            yield OptionList(
                *(Option(f" {i}. {label}", id=vid)
                  for i, (vid, label) in enumerate(self.OPTIONS, start=1)),
                id="confirm-list",
            )

    def on_mount(self) -> None:
        self.query_one("#confirm-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_choose(self, index: int) -> None:
        self.dismiss(self.OPTIONS[index][0])

    def action_cancel(self) -> None:
        self.dismiss("no")


class KeyChoiceScreen(ModalScreen[str]):
    """Ask how to provide a provider's key: type it or use an env var."""

    BINDINGS = [
        Binding("1", "choose(0)", "Enter"),
        Binding("2", "choose(1)", "Variable"),
        Binding("3", "choose(2)", "Cancel"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, provider: str, env_var: str | None) -> None:
        super().__init__()
        self.provider = provider
        self.env_var = env_var
        self.opts: list[tuple[str, str]] = [("enter", "Enter the key now")]
        if env_var:
            self.opts.append(("env", f"Use the environment variable {env_var}"))
        self.opts.append(("cancel", "Cancel"))

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(
                Text.assemble(
                    ("🔑 ", "bold yellow"),
                    (f"{self.provider} needs an API key\n", "bold yellow"),
                    ("How do you want to provide it?", "white"),
                ),
                id="confirm-text",
            )
            yield OptionList(
                *(Option(f" {i}. {label}", id=vid)
                  for i, (vid, label) in enumerate(self.opts, start=1)),
                id="confirm-list",
            )

    def on_mount(self) -> None:
        self.query_one("#confirm-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_choose(self, index: int) -> None:
        if index < len(self.opts):
            self.dismiss(self.opts[index][0])

    def action_cancel(self) -> None:
        self.dismiss("cancel")


class KeyInputScreen(ModalScreen[str]):
    """(Masked) field to type the API key. Returns the key or ''."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, provider: str) -> None:
        super().__init__()
        self.provider = provider

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(
                Text.assemble(
                    ("🔑 API key — ", "bold yellow"),
                    (self.provider, "bold yellow"),
                    ("\nEnter confirms · Esc cancels", "dim"),
                ),
                id="confirm-text",
            )
            inp = Input(placeholder="paste the key here…", password=True, id="key-input")
            yield inp

    def on_mount(self) -> None:
        self.query_one("#key-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss("")


# Footer key order we want, left to right (keys not listed keep their order).
_FOOTER_KEY_ORDER = ("escape",)


class _OrderedFooterScreen(Screen):
    """Default screen that fixes the footer's key order (Esc first).

    Textual orders footer keys by where each key is first seen while walking the
    focus chain. We pin ``escape`` first; this only reorders the *display* dict
    (``active_bindings`` is used by the Footer); key dispatch is unaffected."""

    @property
    def active_bindings(self):
        bindings = super().active_bindings
        ordered = {k: bindings[k] for k in _FOOTER_KEY_ORDER if k in bindings}
        for key, value in bindings.items():
            if key not in ordered:
                ordered[key] = value
        return ordered


# --------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------- #
class CoworkApp(App):
    """Upcode orchestrator TUI application."""

    TITLE = "Upcode"
    SUB_TITLE = "coding agent · orchestrator"

    CSS = """
    Screen { layout: vertical; }
    #log {
        height: 1fr;
        padding: 0 1;
        background: $surface;
    }
    #log Static { margin: 0 0 1 0; }
    #composer {
        height: 3;
        border: round $accent;
        margin: 0 1 1 1;
    }
    #composer:focus { border: round $accent-lighten-2; }
    #cmdmenu {
        display: none;
        height: auto;
        max-height: 8;
        margin: 0 1;
        border: round $accent;
        background: $surface;
    }
    #cmdmenu.open { display: block; }
    #working {
        display: none;
        height: 1;
        padding: 0 2;
        margin: 0 1;
    }
    #working.busy { display: block; }
    ConfirmScreen { align: center middle; }
    #confirm-box {
        width: 70;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }
    #confirm-list { height: auto; margin-top: 1; border: none; background: $surface; }
    #key-input { margin-top: 1; }
    """

    # Footer order is fixed by _OrderedFooterScreen (Esc first); the order here
    # only matters for key dispatch.
    BINDINGS = [
        Binding("escape", "interrupt", "Interrupt"),
        # Clear is hidden from the footer — use the /clear command instead.
        Binding("ctrl+l", "clear", "Clear", show=False),
        # Pasting (Cmd+V on macOS / Ctrl+V on Windows/Linux) is handled by the
        # composer (CommandInput) — text or objects (image/file) as markers.
        Binding("ctrl+c", "confirm_quit", "Quit", priority=True, show=False),
        Binding("ctrl+d", "confirm_quit", "Quit", priority=True, show=False),
    ]

    def get_default_screen(self) -> Screen:
        return _OrderedFooterScreen(id="_default")

    def __init__(self) -> None:
        super().__init__()
        config = AgentConfig()
        apply_workspace(config)  # uses UPCODE_WORKSPACE (or the Upcode home)
        self.agent = Orchestrator(
            agents=default_agents(config.workspace), config=config)
        self._interrupt = False
        self._quit_armed = False  # 1st Ctrl+C/Ctrl+D arms; the 2nd quits
        self._notice_active = False  # transient notice on the thinking bar
        self._busy = False
        self.menu_open = False
        self._menu_mode = "command"  # "command" or "model"
        self._busy_start = 0.0
        self._phase = 0
        self._status = _STAGE_THINKING  # real stage shown in the indicator
        self._auto_approve = False  # "Always" turns off confirmations for the session
        # Pasted objects for the next turn, keyed by their [Kind N] marker number
        # (image → multimodal block; other files → path). Deleting the marker
        # drops the object (see _compose_content). The counter grows per message.
        self._objects: dict[int, dict] = {}
        self._obj_seq = 0
        # Undo (item 4): per-turn file checkpoints; /undo reverts the last one.
        self._snapshots = SnapshotStore()
        # Sessions (item 3): id of the session this run auto-saves to (lazily set
        # on the first turn, or to a resumed session's id).
        self._session_id: str | None = None
        self._apply_settings()  # restore auto/parallel from .upcode/settings.json
        try:
            self.models = load_models()
        except ValueError:
            self.models = {}
        self._apply_last_config()  # restore the last used model

    # -- persistent settings (<workspace>/.upcode/settings.json) -------- #
    def _apply_settings(self) -> None:
        """Load and apply the workspace's remembered toggles (auto/parallel)."""
        data = load_settings(self.agent.config.workspace)
        if "auto_approve" in data:
            self._auto_approve = bool(data["auto_approve"])
        if "parallel" in data:
            self.agent.parallel = bool(data["parallel"])
        if "headless" in data:
            set_headless(bool(data["headless"]))

    def _save_settings(self) -> None:
        """Persist the current toggles to the workspace settings file."""
        save_settings(self.agent.config.workspace, {
            "auto_approve": self._auto_approve,
            "parallel": self.agent.parallel,
            "headless": headless_enabled(),
        })

    def _apply_last_config(self) -> None:
        """Apply a model from models.json: the last saved one (conf/state.json) or,
        in its absence, the first profile that already has a key (or is local)."""
        prof = resolve_last_profile(self.models)
        if prof is None:
            prof = next((p for p in self.models.values() if not needs_api_key(p)), None)
        if prof is None or needs_api_key(prof):
            return
        self.agent.set_llm(prof.model, prof.base_url, prof.api_key, prof.api,
                           prof.max_output, prof.context_window, prof.temperature,
                           input_cost=prof.input_cost, output_cost=prof.output_cost,
                           thinking_budget=thinking_budget_for(prof))

    # -- layout --------------------------------------------------------- #
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield VerticalScroll(id="log")
        yield Static(id="working")
        yield OptionList(id="cmdmenu")
        composer = CommandInput(
            placeholder="Describe a task…  (/ commands · @ agent · ! shell · Ctrl+V image/file · Enter sends · Esc interrupts)",
            id="composer",
        )
        composer.border_title = "›"
        composer.border_subtitle = self._status_label()
        yield composer
        yield Footer()

    def _status_label(self) -> str:
        """Bottom-bar label with the auto-approval state (and plan mode when on).

        (`parallel`/`headless` are shown in the command menu instead.)"""
        return (f"auto: {'on' if self._auto_approve else 'off'}"
                f"  ·  plan: {'on' if self.agent.plan_mode else 'off'}")

    def _refresh_status(self) -> None:
        """Update the auto-approval indicator on the composer's bottom border."""
        self.query_one("#composer", Input).border_subtitle = self._status_label()

    # -- paste: text or objects (image/file) ---------------------------- #
    def _drop_object(self, n: int) -> None:
        """Forget the object behind a ``[Kind n]`` marker that was deleted."""
        self._objects.pop(n, None)

    def _handle_paste(self, comp: Input, text: str | None) -> None:
        """Route a paste: an image/file becomes a marker; text is inserted.

        ``text`` is the native paste payload, or ``None`` for a manual Ctrl+V
        (we read the OS clipboard ourselves)."""
        from .clipboard import grab_image, grab_text
        if text is None:                       # manual Ctrl+V (Windows/Linux)
            image = grab_image()
            if image:
                self._paste_image(comp, *image)
                return
            text = grab_text() or ""
        if not text.strip():                   # empty native paste ⇒ likely an image
            image = grab_image()
            if image:
                self._paste_image(comp, *image)
            return
        paths = self._existing_paths(text)
        if paths:                              # file path(s) copied from a file manager
            for path in paths:
                self._paste_file(comp, path)
            return
        comp.insert_text_at_cursor(text.splitlines()[0])  # plain text

    def _existing_paths(self, text: str) -> list[str]:
        """Path-like lines in ``text`` that point to existing files.

        Requires a path shape (absolute, ``~`` or a separator, or ``file://``)
        so a pasted bare word isn't mistaken for a file."""
        out: list[str] = []
        for raw in text.splitlines():
            cand = raw.strip().strip("\"'")
            is_url = cand.startswith("file://")
            if is_url:
                from urllib.parse import unquote, urlparse
                cand = unquote(urlparse(cand).path)
            elif not (os.path.isabs(cand) or cand.startswith("~") or os.sep in cand):
                continue
            cand = os.path.expanduser(cand)
            if cand and os.path.isfile(cand):
                out.append(cand)
        return out

    def _paste_image(self, comp: Input, media_type: str, data: str) -> None:
        self._obj_seq += 1
        n = self._obj_seq
        self._objects[n] = {"kind": "Image",
                            "block": {"type": "image", "media_type": media_type,
                                      "data": data}}
        comp.insert_text_at_cursor(f"[Image {n}] ")
        self._notice(f"image pasted as [Image {n}] "
                     "(delete the marker to remove it)", 2.5)

    def _paste_file(self, comp: Input, path: str) -> None:
        self._obj_seq += 1
        n = self._obj_seq
        kind = _classify_kind(path)
        block = _load_image_block(path) if kind == "Image" else None
        if kind == "Image" and block is None:
            kind = "File"
        if block is not None:
            self._objects[n] = {"kind": "Image", "block": block}
        else:
            self._objects[n] = {"kind": kind, "path": os.path.abspath(path)}
        marker = f"[{kind} {n}]"
        comp.insert_text_at_cursor(marker + " ")
        self._notice(f"pasted {marker} (delete the marker to remove it)", 2.5)

    def on_mount(self) -> None:
        cfg = self.agent.config
        branch = self._git_branch()
        self._write(Text.assemble(
            (">_ ", "bold cyan"),
            ("Upcode", "bold"),
            (" — coding agent: explores, plans, edits and runs the project.\n", "dim"),
            (f"model {cfg.model}   ", "dim"),
            (f"dir {self._cwd()}   ", "dim"),
            *(((f"git {branch}   ", "dim"),) if branch else ()),
            (f"agents {len(self.agent.agents)}", "dim"),
        ))
        # Report MCP servers connected during construction (if any).
        for line in self.agent.mcp.log_lines:
            self._write(Text(line, style="dim"))
        self.query_one("#composer", Input).focus()
        # Timer for the "thinking" indicator animation (Codex style).
        self.set_interval(0.09, self._tick)
        # Confirm before writing/deleting files.
        set_confirm_hook(self._confirm)
        # Snapshot files before the agent changes them (for /undo).
        set_change_hook(self._snapshots.record)

    def on_unmount(self) -> None:
        set_confirm_hook(None)
        set_change_hook(None)
        set_read_only(False)  # clear the process-wide plan-mode guard
        self.agent.shutdown()  # stop MCP servers

    # -- file-change confirmation --------------------------------------- #
    def _confirm(self, action: str, path: str) -> bool:
        """Called from the worker thread; opens the modal in the UI and waits for the choice."""
        if self._auto_approve:
            return True
        result: dict[str, str] = {}
        done = threading.Event()

        def ask() -> None:
            def chosen(value: str | None) -> None:
                result["v"] = value or "no"
                done.set()
            self.push_screen(ConfirmScreen(action, path), chosen)

        self.call_from_thread(ask)
        done.wait()
        choice = result.get("v", "no")
        if choice == "always":
            self._auto_approve = True
            self._save_settings()
            self.call_from_thread(self._refresh_status)
        return choice in ("yes", "always")

    # -- utilities ------------------------------------------------------ #
    def _cwd(self) -> str:
        cwd, home = self.agent.config.workspace, os.path.expanduser("~")
        return "~" + cwd[len(home):] if cwd.startswith(home) else cwd

    def _git_branch(self) -> str:
        """Current branch + '*' if there are uncommitted changes (or '' if not a git repo)."""
        try:
            ws = self.agent.config.workspace
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, cwd=ws, timeout=5,
            )
            if branch.returncode != 0:
                return ""
            dirty = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, cwd=ws, timeout=5,
            )
            mark = "*" if dirty.stdout.strip() else ""
            return branch.stdout.strip() + mark
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return ""

    def _write(self, renderable) -> Static:
        """Add a new block to the conversation and scroll to the end."""
        widget = Static(renderable)
        log = self.query_one("#log", VerticalScroll)
        log.mount(widget)
        log.scroll_end(animate=False)
        return widget

    def _update_live(self, widget: Static, text: Text) -> None:
        widget.update(text)
        self.query_one("#log", VerticalScroll).scroll_end(animate=False)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        working = self.query_one("#working", Static)
        if busy:
            self._busy_start = time.monotonic()
            self._phase = 0
            self._status = _STAGE_THINKING
            working.add_class("busy")
        else:
            working.remove_class("busy")
        self.sub_title = "thinking…" if busy else "coding agent · orchestrator"

    def _tick(self) -> None:
        """Update the work indicator animation on every frame."""
        if self._notice_active:
            return  # don't overwrite a transient notice
        if not self._busy:
            return
        self._phase += 1
        frame = _SPINNER[self._phase % len(_SPINNER)]
        word = self._status  # real stage (updated by the agent's events)
        elapsed = int(time.monotonic() - self._busy_start)
        line = Text()
        line.append(frame + " ", style="cyan")
        line.append_text(_shimmer(word, self._phase // 2))
        line.append(f"  ({elapsed}s · Esc to interrupt)", style="grey37")
        self.query_one("#working", Static).update(line)

    # -- user input ----------------------------------------------------- #
    def on_input_submitted(self, event: Input.Submitted) -> None:
        user = event.value.strip()
        event.input.value = ""
        if not user:
            self._objects.clear()  # nothing to send; drop any pasted objects
            self._obj_seq = 0
            return
        if user in ("/quit", "/exit"):
            self.exit()  # always quits, even with a turn in progress
            return
        if self._busy:
            return  # ignore while a turn is in progress
        if user.startswith("/"):
            self._command(user)
            return
        if user.startswith("!"):
            command = user[1:].strip()
            if command:
                self._run_shell(command)
            return
        if user.startswith("@"):
            self._run_agent_command(user)
            return

        content = self._compose_content(user)
        self._write(Text.assemble(("› ", "bold magenta"), (user, "bold")))
        live = self._write(Text(""))
        self._interrupt = False
        self._set_busy(True)
        self.run_turn(content, live, label=user)

    def _compose_content(self, user: str) -> str | list[dict]:
        """Build the turn content from the text and the objects it still marks.

        Each ``[Kind N]`` marker still present pulls in its pasted object
        (deleting the marker drops it). Images become multimodal blocks; other
        files are listed by path so the agent can open them with its file tools.
        Markers are kept in the text so the model knows where each was referenced."""
        objs, self._objects, self._obj_seq = self._objects, {}, 0
        if not objs:
            return user
        image_blocks: list[dict] = []
        notes: list[str] = []
        seen: set[int] = set()
        for match in _MARKER_RE.finditer(user):
            n = int(match.group(2))
            if n not in objs or n in seen:
                continue
            seen.add(n)
            obj = objs[n]
            if obj.get("block"):
                image_blocks.append(obj["block"])
            elif obj.get("path"):
                notes.append(f"{match.group(0)} → {obj['path']}")
        text = user if not notes else user + "\n\nAttached files:\n" + "\n".join(notes)
        if image_blocks:
            return [{"type": "text", "text": text}, *image_blocks]
        return text

    def _run_agent_command(self, user: str) -> None:
        """Invoke an agent directly: ``@name task`` (without going through the router)."""
        name, _, task = user[1:].partition(" ")
        name, task = name.strip(), task.strip()
        # resolve the name case-insensitively
        real = next((s.name for s in self.agent.agents
                     if s.name.lower() == name.lower()), None)
        if real is None:
            available = ", ".join(self.agent.agents.names()) or "(none)"
            self._write(Text(f"unknown agent: {name}. Available: {available}",
                             style="red"))
            return
        if not task:
            self._write(Text(f"usage: @{real} <task>", style="dim"))
            return
        self._write(Text.assemble(("@", "bold magenta"), (real, "bold magenta"),
                                  (f" {task}", "bold")))
        live = self._write(Text(""))
        self._interrupt = False
        self._set_busy(True)
        self.run_agent_turn(real, task, live)

    # -- command menu (when typing "/") --------------------------------- #
    def on_input_changed(self, event: Input.Changed) -> None:
        value = event.value
        if value.startswith("/model "):          # models submenu
            self._open_model_menu(value[len("/model "):].strip())
        elif value.startswith("@") and " " not in value:  # agents menu
            self._open_agent_menu(value[1:])
        elif value.startswith("/") and " " not in value:
            self._open_menu(value)
        else:
            self._close_menu()

    def _open_menu(self, prefix: str) -> None:
        def matches_prefix(cmd: str) -> bool:
            # matches by the command itself OR by an alias that points to it (e.g. /e → /quit)
            if cmd.startswith(prefix):
                return True
            return any(a.startswith(prefix) for a, canon in COMMAND_ALIASES.items()
                       if canon == cmd)

        matches = [(c, d) for c, d in COMMANDS if matches_prefix(c)]
        menu = self.query_one("#cmdmenu", OptionList)
        menu.clear_options()
        if not matches:
            self._close_menu()
            return
        for cmd, desc in matches:
            parts = [(cmd, "bold"), (f"   {desc}", "dim")]
            state = self._toggle_state(cmd)
            if state is not None:
                parts.append((f"  [{'on' if state else 'off'}]",
                              "bold green" if state else "yellow"))
            menu.add_option(Option(Text.assemble(*parts), id=cmd))
        menu.add_class("open")
        menu.highlighted = 0
        self.menu_open = True
        self._menu_mode = "command"

    def _toggle_state(self, cmd: str) -> bool | None:
        """Current on/off state for a toggle command (``None`` if not a toggle).

        Lets the command menu show the live `[on]/[off]` state next to each
        toggle (`/auto`, `/parallel`, `/headless`, `/plan`)."""
        if cmd == "/auto":
            return self._auto_approve
        if cmd == "/parallel":
            return self.agent.parallel
        if cmd == "/headless":
            return headless_enabled()
        if cmd == "/plan":
            return self.agent.plan_mode
        return None

    def _open_model_menu(self, partial: str) -> None:
        menu = self.query_one("#cmdmenu", OptionList)
        menu.clear_options()
        term = partial.lower()
        # filter by substring in the name OR the label (e.g. "claude", "gpt", "1m")
        matches = [(n, p) for n, p in self.models.items()
                   if term in n.lower() or term in (p.label or "").lower()]
        if not matches:
            self._close_menu()
            return
        for name, p in matches:
            label = f"  {p.label}" if p.label else ""
            ctx = f" · ctx {_human(p.context_window)}" if p.context_window else ""
            out = f" · out {_human(p.max_output)}" if p.max_output else ""
            menu.add_option(Option(
                Text.assemble((name, "bold"), (f"{label}   [{p.model}{ctx}{out}]", "dim")),
                id=f"model:{name}"))
        menu.add_class("open")
        menu.highlighted = 0
        self.menu_open = True
        self._menu_mode = "model"

    def _open_agent_menu(self, partial: str) -> None:
        """Agents menu (when typing ``@``): filters by substring in the name."""
        menu = self.query_one("#cmdmenu", OptionList)
        menu.clear_options()
        term = partial.lower()
        matches = [s for s in self.agent.agents if term in s.name.lower()]
        if not matches:
            self._close_menu()
            return
        # usable width inside the panel (margins + borders + headroom for the scrollbar);
        # truncate the description so the option fits on ONE line (OptionList wraps text).
        avail = max((self.size.width or 80) - 8, 24)
        for s in matches:
            prefix = f"@{s.name}"
            desc = _short(s.description, max(avail - len(prefix) - 3, 8))
            menu.add_option(Option(
                Text.assemble((prefix, "bold"), (f"   {desc}", "dim")),
                id=f"agent:{s.name}"))
        menu.add_class("open")
        menu.highlighted = 0
        self.menu_open = True
        self._menu_mode = "agent"

    def _close_menu(self) -> None:
        if self.menu_open:
            self.query_one("#cmdmenu", OptionList).remove_class("open")
            self.menu_open = False

    def handle_menu_key(self, key: str) -> None:
        menu = self.query_one("#cmdmenu", OptionList)
        if key == "down":
            menu.action_cursor_down()
        elif key == "up":
            menu.action_cursor_up()
        elif key == "escape":
            self._close_menu()
        elif key in ("enter", "tab"):
            self._accept_menu()

    def _accept_menu(self) -> None:
        menu = self.query_one("#cmdmenu", OptionList)
        if menu.highlighted is None:
            return
        self._activate_option(menu.get_option_at_index(menu.highlighted).id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self._activate_option(event.option.id)

    def _activate_option(self, option_id: str) -> None:
        if option_id.startswith("model:"):           # picked a model in the submenu
            self._run_command(f"/model {option_id[len('model:'):]}")
        elif option_id.startswith("agent:"):          # agent: fills "@name " for the task
            self._close_menu()
            inp = self.query_one("#composer", Input)
            inp.value = f"@{option_id[len('agent:'):]} "
            inp.cursor_position = len(inp.value)
        elif option_id in ARG_COMMANDS:               # command with an argument: fills it in
            self._close_menu()
            inp = self.query_one("#composer", Input)
            inp.value = option_id + " "
            inp.cursor_position = len(inp.value)
        else:
            self._run_command(option_id)

    def _run_command(self, cmd: str) -> None:
        self._close_menu()
        self.query_one("#composer", Input).value = ""
        self._command(cmd)

    def _command(self, cmd: str) -> None:
        parts = cmd.split(maxsplit=1)
        name = parts[0]
        arg = parts[1].strip() if len(parts) > 1 else ""

        if name in ("/quit", "/exit"):
            self.exit()
        elif name == "/clear":
            self.action_clear()
        elif name == "/compact":
            self._do_compact()
        elif name == "/model":
            self._set_model(arg)
        elif name == "/workspace":
            self._set_workspace(arg)
        elif name == "/reset":
            self.agent.reset()
            self._session_id = None  # next turn starts a fresh session file
            self._write(Text("context cleared.", style="dim"))
        elif name == "/status":
            cfg = self.agent.config
            used, budget = self.agent.context_status()
            window = _human(cfg.context_window) if cfg.context_window else "∞ (unlimited)"
            u = self.agent.usage
            thinking = (f"  thinking {_human(cfg.thinking_budget)}"
                        if cfg.thinking_budget else "")
            cost = self.agent.cost()
            cost_txt = (f"  ·  cost ${cost:.4f}" if (cfg.input_cost or cfg.output_cost)
                        else "  ·  cost n/a")
            self._write(Text("\n".join([
                f"model     {cfg.model}  (api: {cfg.api}){thinking}",
                f"endpoint  {cfg.base_url or 'OpenAI default'}",
                f"context   ~{_human(used)} / {window}   max output  {_human(cfg.max_output)}",
                f"usage     in {_human(u.input_tokens)} · out {_human(u.output_tokens)} tokens{cost_txt}",
                f"workspace {cfg.workspace}",
                f"agents    {', '.join(self.agent.agents.names())}",
                f"mode      {'plan (read-only)' if self.agent.plan_mode else 'build'}",
            ]), style="dim"))
        elif name in ("/agents", "/tools"):
            t = Text()
            for s in self.agent.agents:
                t.append("• ", style="cyan")
                t.append(s.name, style="bold")
                t.append(f" — {s.description}\n", style="dim")
            self._write(t)
        elif name == "/skills":
            skills = load_skills()
            if not skills:
                self._write(Text("no skills in .upcode/skills/", style="dim"))
            else:
                t = Text()
                for s in skills.values():
                    t.append("• ", style="cyan")
                    t.append(s.name, style="bold")
                    t.append(f" — {s.description}\n", style="dim")
                self._write(t)
        elif name == "/mcp":
            self._show_mcp()
        elif name == "/rules":
            self._show_rules()
        elif name == "/init":
            self._init_rules()
        elif name == "/diff":
            self._show_diff()
        elif name == "/undo":
            self._undo()
        elif name == "/sessions":
            self._show_sessions()
        elif name == "/resume":
            self._resume_session(arg)
        elif name == "/plan":
            self._set_plan_mode(not self.agent.plan_mode)
        elif name == "/auto":
            self._auto_approve = not self._auto_approve
            self._refresh_status()
            self._save_settings()
            state = "ON" if self._auto_approve else "off"
            self._write(Text(
                f"auto-approval {state} — edits and commands "
                + ("run without confirmation." if self._auto_approve
                   else "ask for confirmation again."),
                style="yellow" if self._auto_approve else "dim"))
        elif name == "/parallel":
            self.agent.parallel = not self.agent.parallel
            self._save_settings()
            state = "ON" if self.agent.parallel else "off"
            self._write(Text(
                f"parallel execution {state} — `delegate_parallel` "
                + ("runs independent agents concurrently."
                   if self.agent.parallel
                   else "runs agents one at a time (sequential)."),
                style="yellow" if self.agent.parallel else "dim"))
        elif name == "/headless":
            set_headless(not headless_enabled())
            on = headless_enabled()
            self._save_settings()
            state = "ON" if on else "off"
            self._write(Text(
                f"headless browser {state} — `browser_test` runs "
                + ("without a visible window."
                   if on else "with a visible window so you can watch it."),
                style="yellow" if on else "dim"))
        elif name == "/help":
            self._write(Text(
                "Commands: /model [name]  /compact  /workspace [dir]  /status  /agents  /skills  /mcp  /rules  /init  /diff  /undo  /sessions  /resume [id]  /plan  /auto  /parallel  /headless  /reset  /clear  /help  /quit\n"
                "Agent:    prefix with @ to invoke an agent directly (e.g. @qatester test the site www.google.com)\n"
                "Shell:    prefix with ! to run a command in the workspace (e.g. !ls -la, !git status)\n"
                "Paste:    Ctrl+V pastes the clipboard — an image/file becomes a [Kind N] marker (delete it to remove); plain text inserts (Cmd+V also works for text)\n"
                "Keys:     Enter sends · Esc interrupts · /clear clears the screen · Ctrl+C/Ctrl+D 2× to quit",
                style="dim",
            ))
        else:
            self._write(Text(f"unknown command: {name}", style="red"))

    def _show_mcp(self) -> None:
        """List connected MCP servers and the tools they expose."""
        mcp = self.agent.mcp
        if not mcp.servers:
            self._write(Text(
                "no MCP servers connected — configure them in "
                "conf/mcp.json or <workspace>/.upcode/mcp.json.", style="dim"))
            return
        t = Text()
        for server in mcp.servers:
            tools = mcp.catalog.get(server.name, [])
            t.append("• ", style="cyan")
            t.append(server.name, style="bold")
            t.append(f"  ({len(tools)} tool(s))\n", style="dim")
            for name in tools:
                t.append(f"    └ {name}\n", style="dim")
        self._write(t)

    def _show_rules(self) -> None:
        """List the project rules files in effect (AGENTS.md/UPCODE.md)."""
        workspace = self.agent.config.workspace
        files = find_rules_files(workspace)
        if not files:
            self._write(Text(
                f"no project rules found — run /init to create "
                f"{rules_filename()}.", style="dim"))
            return
        t = Text()
        t.append("project rules in effect (auto-loaded into the prompt):\n",
                 style="dim")
        for p in files:
            rel = os.path.relpath(p, workspace)
            display = rel if not rel.startswith("..") else p
            t.append("• ", style="cyan")
            t.append(f"{display}\n", style="bold")
        self._write(t)

    def _init_rules(self) -> None:
        """Generate an AGENTS.md skeleton by inspecting the repository."""
        workspace = self.agent.config.workspace
        target = os.path.join(workspace, rules_filename())
        if os.path.exists(target):
            self._write(Text(
                f"{rules_filename()} already exists at {target} — edit it or "
                "delete it first. Showing current rules with /rules.", style="yellow"))
            return
        try:
            content = generate_rules_skeleton(workspace)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(content)
                if not content.endswith("\n"):
                    fh.write("\n")
        except OSError as exc:
            self._write(Text(f"could not create {rules_filename()}: {exc}",
                             style="red"))
            return
        # Reload agents (their prompts embed the rules) and refresh the
        # orchestrator's system prompt in place, keeping the conversation.
        self.agent.reload_agents(default_agents(workspace), reset=False)
        self._write(Text(
            f"created {rules_filename()} ({target}) and loaded it into the "
            "prompt — review and fill it in. Edit then /reset (or /workspace .) "
            "to reload changes.", style="green"))

    def _set_plan_mode(self, enabled: bool) -> None:
        """Switch read-only plan mode (item 6) on/off and report the new state."""
        self.agent.set_plan_mode(enabled)
        self._refresh_status()
        if enabled:
            self._write(Text(
                "🔒 plan mode ON (read-only) — the agent investigates and proposes "
                "a plan; edits, deletes and commands are disabled. Run /plan again "
                "to turn it off and execute.", style="yellow"))
        else:
            self._write(Text(
                "🛠 plan mode OFF — edits and commands are enabled again.",
                style="green"))

    def _undo(self) -> None:
        """Revert the file changes captured in the last turn's checkpoint."""
        if self._busy:
            self._write(Text("can't undo while a turn is running.", style="dim"))
            return
        result = self._snapshots.undo()
        if result is None:
            self._write(Text("nothing to undo — no file changes recorded yet.",
                             style="dim"))
            return
        ws = self.agent.config.workspace
        t = Text()
        t.append(f"↩ reverted {len(result['restored'])} file(s):\n", style="green")
        for ap in result["restored"]:
            rel = os.path.relpath(ap, ws)
            t.append(f"  • {rel if not rel.startswith('..') else ap}\n", style="dim")
        for ap in result["failed"]:
            t.append(f"  ⨯ failed: {ap}\n", style="red")
        t.append("(file edits only — the conversation is unchanged)", style="dim")
        self._write(t)

    def _show_sessions(self) -> None:
        """List the saved sessions for the current workspace."""
        from .session import sessions_dir
        directory = sessions_dir(self.agent.config.workspace)
        sessions = list_sessions(self.agent.config.workspace)
        if not sessions:
            self._write(Text(
                "no saved sessions in this workspace — they are auto-saved per "
                f"turn under:\n  {directory}\n"
                "sessions are per-workspace; switch with /workspace <dir> if "
                "yours is elsewhere.", style="dim"))
            return
        t = Text(f"saved sessions in {directory} (newest first) — "
                 "resume with /resume <id>:\n", style="dim")
        for s in sessions:
            sid = s.get("id", "?")
            mark = " (current)" if sid == self._session_id else ""
            count = sum(1 for m in s.get("messages", [])
                        if m.get("role") in ("user", "assistant"))
            t.append("• ", style="cyan")
            t.append(sid, style="bold")
            t.append(f"{mark}  {s.get('updated', '')}  ", style="dim")
            t.append(f"{_short(s.get('title', ''), 50)}", style="white")
            t.append(f"  ({count} msg)\n", style="dim")
        self._write(t)

    def _resume_session(self, arg: str) -> None:
        """Resume a saved session by id, restoring its conversation history."""
        if self._busy:
            self._write(Text("can't resume while a turn is running.", style="dim"))
            return
        if not arg:
            self._show_sessions()
            self._write(Text("usage: /resume <id>", style="dim"))
            return
        data = load_session(self.agent.config.workspace, arg)
        if data is None:
            self._write(Text(
                f"session '{arg}' not found — see /sessions.", style="red"))
            return
        # Restore the history, then refresh the system prompt in place so the
        # current workspace's agents/skills/rules apply (keeps the conversation).
        self.agent.messages = list(data["messages"])
        self.agent.apply_system_prompt(reset=False)
        self._session_id = data["id"]
        count = sum(1 for m in data["messages"]
                    if m.get("role") in ("user", "assistant"))
        self._write(Text(
            f"↻ resumed session {data['id']} ({count} msg) — "
            f"{_short(data.get('title', ''), 50)}", style="green"))

    def _autosave_session(self) -> None:
        """Persist the conversation to its session file (best-effort, per turn)."""
        if not has_content(self.agent.messages):
            return
        if self._session_id is None:
            self._session_id = new_session_id()
        save_session(self.agent.config.workspace, self._session_id,
                     self.agent.messages, model=self.agent.config.model)

    def _show_diff(self) -> None:
        """Show the working tree's `git diff`, colorized."""
        try:
            proc = subprocess.run(
                ["git", "diff", "--no-color"],
                capture_output=True, text=True, cwd=self.agent.config.workspace,
                timeout=15,
            )
        except FileNotFoundError:
            self._write(Text("git not found in PATH.", style="red"))
            return
        except (subprocess.SubprocessError, OSError) as exc:
            self._write(Text(f"error running git diff: {exc}", style="red"))
            return
        if proc.returncode != 0:
            msg = (proc.stderr or "").strip() or "directory is not a git repository."
            self._write(Text(msg, style="red"))
            return
        out = proc.stdout.strip()
        if not out:
            self._write(Text("no uncommitted changes in the working tree.", style="dim"))
            return
        self._write(_render_diff(out, max_lines=400))

    def _set_model(self, arg: str) -> None:
        """List (without an argument) or select a model from models.json."""
        if not self.models:
            self._write(Text(
                "no models configured. Create a conf/models.json in the Upcode "
                "home (or set UPCODE_HOME_DIR).", style="red"))
            return
        cfg = self.agent.config
        if not arg:
            t = Text("configured models:\n", style="dim")
            for name, p in self.models.items():
                current = p.model == cfg.model and p.base_url == cfg.base_url
                t.append("→ " if current else "  ", style="green")
                t.append(name, style="bold")
                label = f"  {p.label}" if p.label else ""
                windows = f" · ctx {_human(p.context_window)} · out {_human(p.max_output)}"
                t.append(f"{label}   [{p.model}{windows}]\n", style="dim")
            t.append("use: /model <name>  (filters by name or provider)", style="dim")
            self._write(t)
            return
        prof = self.models.get(arg)
        if prof is None:
            self._write(Text(f"model '{arg}' is not in models.json", style="red"))
            return
        if needs_api_key(prof):
            self._prompt_key(prof)   # key missing: ask how to provide it
        else:
            self._apply_model(prof)  # local or key already available

    # -- API key provisioning ------------------------------------------- #
    def _prompt_key(self, prof: ModelProfile) -> None:
        def on_choice(choice: str | None) -> None:
            if choice == "enter":
                self._prompt_key_input(prof)
            elif choice == "env":
                value = os.getenv(prof.api_key_env) if prof.api_key_env else None
                if value:
                    self._apply_model(prof, value)
                else:
                    self._write(Text(
                        f"the variable {prof.api_key_env} is not set — "
                        f"set it (e.g. in .env) and select again.", style="red"))
        self.push_screen(KeyChoiceScreen(prof.provider or prof.name, prof.api_key_env), on_choice)

    def _prompt_key_input(self, prof: ModelProfile) -> None:
        def on_key(value: str | None) -> None:
            if value:
                self._apply_model(prof, value)
            else:
                self._write(Text("selection cancelled (no key).", style="dim"))
        self.push_screen(KeyInputScreen(prof.provider or prof.name), on_key)

    def _apply_model(self, prof: ModelProfile, api_key: str | None = None) -> None:
        self.agent.set_llm(prof.model, prof.base_url, api_key or prof.api_key,
                           prof.api, prof.max_output, prof.context_window,
                           prof.temperature, input_cost=prof.input_cost,
                           output_cost=prof.output_cost,
                           thinking_budget=thinking_budget_for(prof))
        save_last_config(prof)  # remember the choice for the next session
        self.sub_title = "coding agent · orchestrator"
        cfg = self.agent.config
        self._write(Text(
            f"model selected: {prof.name}  ({prof.model})  "
            f"ctx {_human(cfg.context_window)} · out {_human(cfg.max_output)}"
            "  — context cleared",
            style="green"))

    def _set_workspace(self, arg: str) -> None:
        """Show (without an argument) or change the working directory."""
        cfg = self.agent.config
        if not arg:
            self._write(Text(f"current workspace: {cfg.workspace}", style="dim"))
            return
        path = os.path.abspath(os.path.expanduser(arg))
        if not os.path.isdir(path):
            self._write(Text(f"directory not found: {path}", style="red"))
            return
        try:
            os.chdir(path)  # tools resolve relative paths from here
        except OSError as exc:
            self._write(Text(f"error changing directory: {exc}", style="red"))
            return
        cfg.workspace = path
        self.agent.base_config.workspace = path
        # New project = new .agents/.skills/rules: reload the roster and rebuild
        # the system prompt (clears the conversation).
        self.agent.reload_agents(default_agents(path))
        self._session_id = None  # sessions are per-workspace; start fresh here
        # Settings are per-workspace: load the new one's remembered toggles.
        self._apply_settings()
        self._refresh_status()
        rules = find_rules_files(path)
        extra = (f" · rules: {', '.join(os.path.relpath(r, path) for r in rules)}"
                 if rules else "")
        self._write(Text(f"workspace set: {path}{extra}  — context cleared",
                         style="green"))

    # -- actions (keys) ------------------------------------------------- #
    def action_interrupt(self) -> None:
        if self._busy:
            self._interrupt = True

    def action_confirm_quit(self) -> None:
        """Ctrl+C / Ctrl+D: ask for a second confirmation before quitting.

        The notice shows on the thinking bar for 2s (= window for the 2nd press)."""
        if self._quit_armed:
            self.exit()
            return
        self._quit_armed = True
        self._notice("press Ctrl+C (or Ctrl+D) again to quit", 2.0)

    def _notice(self, text: str, seconds: float) -> None:
        """Show a transient notice on the thinking bar (#working)."""
        self._notice_active = True
        working = self.query_one("#working", Static)
        working.add_class("busy")  # make the bar visible
        working.update(Text(text, style="yellow"))
        self.set_timer(seconds, self._clear_notice)

    def _clear_notice(self) -> None:
        self._notice_active = False
        self._quit_armed = False  # the 2nd-press window expired
        if not self._busy:
            self.query_one("#working", Static).remove_class("busy")

    def action_clear(self) -> None:
        self.query_one("#log", VerticalScroll).remove_children()

    # -- context: warning and compaction -------------------------------- #
    def _finish_turn(self) -> None:
        """End the turn: finalize the undo checkpoint, auto-save, warn on context."""
        self._set_busy(False)
        self._snapshots.commit()   # finalize this turn's checkpoint (item 4)
        self._autosave_session()   # persist the conversation (item 3)
        used, budget = self.agent.context_status()
        if budget and used >= 0.85 * budget:
            level = "full" if used >= budget else "almost full"
            self._write(Text(
                f"⚠ context {level}: ~{_human(used)}/{_human(budget)} — use /compact",
                style="yellow"))

    def _do_compact(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._compact_worker()

    @work(thread=True, exclusive=True)
    def _compact_worker(self) -> None:
        error = None
        try:
            summary = self.agent.compact()
        except Exception as exc:  # noqa: BLE001
            error, summary = str(exc), ""
        self.call_from_thread(self._compact_done, error, summary)

    def _compact_done(self, error: str | None, summary: str) -> None:
        self._set_busy(False)
        if error:
            self._write(Text(f"error compacting: {error}", style="red"))
            return
        if not summary:
            self._write(Text("nothing to compact yet.", style="dim"))
            return
        used, budget = self.agent.context_status()
        target = f"/{_human(budget)}" if budget else ""
        self._write(Text(
            f"✓ history compacted — context ~{_human(used)}{target}", style="green"))

    # -- shell command execution ("!" prefix) --------------------------- #
    def _run_shell(self, command: str) -> None:
        """Run ``command`` in the shell, inside the workspace, and show the output."""
        self._write(Text.assemble(("$ ", "bold green"), (command, "bold")))
        live = self._write(Text("", style="dim"))
        self._set_busy(True)
        self._shell_worker(command, live)

    @work(thread=True, exclusive=True)
    def _shell_worker(self, command: str, live: Static) -> None:
        out = Text()
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                cwd=self.agent.config.workspace, timeout=120,
            )
            if proc.stdout:
                out.append(proc.stdout)
            if proc.stderr:
                out.append(proc.stderr, style="red")
            if proc.returncode != 0:
                out.append(f"\n[exit {proc.returncode}]", style="yellow")
        except subprocess.TimeoutExpired:
            out.append("[command exceeded the 120s time limit]", style="red")
        except OSError as exc:
            out.append(f"error running: {exc}", style="red")
        self.call_from_thread(self._shell_done, live, out)

    def _shell_done(self, live: Static, out: Text) -> None:
        self._set_busy(False)
        live.update(self._truncate(out) if out.plain else Text("(no output)", style="dim"))
        self.query_one("#log", VerticalScroll).scroll_end(animate=False)

    @staticmethod
    def _truncate(text: Text, max_lines: int = 300) -> Text:
        """Limit the displayed output, appending a notice if it was cut."""
        lines = text.plain.splitlines()
        if len(lines) <= max_lines:
            return text
        out = Text("\n".join(lines[:max_lines]) + "\n")
        out.append(f"… (+{len(lines) - max_lines} line(s) omitted)", style="dim")
        return out

    # -- turn execution (separate thread) ------------------------------- #
    @work(thread=True, exclusive=True)
    def run_turn(self, content: str | list, live: Static,
                 label: str | None = None) -> None:
        builder = TurnBuilder()
        # ``content`` may be a multimodal list; use the typed text as the label.
        self._snapshots.begin(label if label is not None else
                              content if isinstance(content, str) else "")  # undo checkpoint

        def refresh() -> None:
            self.call_from_thread(self._update_live, live, builder.text.copy())

        def on_delegate(agent_name: str, task: str) -> None:
            self._status = f"Coordinating · {agent_name}"
            builder.delegate(agent_name, task)
            refresh()

        def on_event(agent_name: str, ev: Event) -> None:
            if ev.kind == "tool_call":
                self._status = f"{_stage_for_tool(ev.name)} · {agent_name}"
            elif ev.kind == "text":
                self._status = f"Coordinating · {agent_name}"
            builder.delegated_event(agent_name, ev)
            refresh()

        self.agent.on_delegate = on_delegate
        self.agent.on_event = on_event
        self.agent.should_stop = lambda: self._interrupt  # Esc stops the agent

        try:
            for ev in self.agent.events(content):
                if self._interrupt:
                    builder.orchestrator_text("")
                    builder.text.append("\n⨯ interrupted", style="red")
                    refresh()
                    break
                if ev.kind == "tool_call" and ev.name != "delegate":
                    self._status = _stage_for_tool(ev.name)
                elif ev.kind == "text":
                    self._status = "Writing"
                builder.agent_event(ev)
                refresh()
        except Exception as exc:  # noqa: BLE001
            builder.text.append(f"\nerror: {exc}", style="red")
            refresh()
        finally:
            self.call_from_thread(self._finish_turn)

    @work(thread=True, exclusive=True)
    def run_agent_turn(self, name: str, task: str, live: Static) -> None:
        """Run an agent directly (without the router), streaming its events."""
        builder = TurnBuilder()
        self._snapshots.begin(task)  # open this turn's undo checkpoint

        def refresh() -> None:
            self.call_from_thread(self._update_live, live, builder.text.copy())

        def on_event(ev: Event) -> None:
            if ev.kind == "tool_call":
                self._status = f"{_stage_for_tool(ev.name)} · {name}"
            elif ev.kind == "text":
                self._status = f"Coordinating · {name}"
            builder.delegated_event(name, ev)
            refresh()

        self._status = f"Coordinating · {name}"
        builder.delegate(name, task)  # header "• name  task"
        refresh()
        try:
            self.agent.agents.run(
                name, task, self.agent.base_config,
                observer=on_event, should_stop=lambda: self._interrupt,
            )
            if self._interrupt:
                builder.text.append("\n⨯ interrupted", style="red")
                refresh()
        except Exception as exc:  # noqa: BLE001
            builder.text.append(f"\nerror: {exc}", style="red")
            refresh()
        finally:
            self.call_from_thread(self._finish_turn)


def main() -> int:
    import sys

    # With arguments, runs in headless mode and exits; without arguments, opens
    # the interactive TUI.  Headless takes the prompt via -p/--prompt, e.g.:
    #   upcode -p "review the code"
    args = sys.argv[1:]
    if args:
        from .headless import main as headless_main
        return headless_main(args)

    CoworkApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
