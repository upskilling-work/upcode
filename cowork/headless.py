"""Upcode headless (non-interactive) mode.

Runs a single prompt straight from the command line, without opening the TUI,
and prints the result to stdout. The prompt is passed with ``-p``/``--prompt``.
Useful for automation and one-off use:

    upcode -p "review the code"
    upcode --model gpt-4o -p "explain this project"
    echo "summarize the README" | upcode -p -    # reads the prompt from stdin

File edits and commands are auto-approved (there is nowhere to confirm
interactively). The exit code is 0 on success and 1 on error.
"""

from __future__ import annotations

import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

from rich.console import Console
from rich.text import Text

from .agent import AgentConfig, Event, apply_workspace
from .builtin_tools import set_confirm_hook
from .manager import Orchestrator
from .models import (
    load_models,
    needs_api_key,
    resolve_last_profile,
    thinking_budget_for,
)
from .agents import default_agents


def _short(text: str, limit: int = 84) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_args(arguments: str) -> str:
    import json
    try:
        data = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return (arguments or "").strip()
    return ", ".join(f"{k}={v!r}" for k, v in data.items())


class _Renderer:
    """Print the events from the orchestrator and the agents to stdout."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self._at_line_start = True

    def _newline_if_needed(self) -> None:
        if not self._at_line_start:
            self.console.print()
            self._at_line_start = True

    def orchestrator_text(self, chunk: str) -> None:
        if not chunk:
            return
        self.console.print(chunk, end="", highlight=False, markup=False)
        self._at_line_start = chunk.endswith("\n")

    def delegate(self, agent_name: str, task: str) -> None:
        self._newline_if_needed()
        self.console.print(Text.assemble(
            ("• ", "cyan"), (agent_name, "bold"),
            (f"  {_short(task, 72)}", "dim"),
        ))

    def delegated_event(self, agent_name: str, ev: Event) -> None:
        if ev.kind == "text":
            self.console.print(Text(ev.text, style="dim"), end="",
                               highlight=False, markup=False)
            self._at_line_start = ev.text.endswith("\n")
        elif ev.kind == "tool_call":
            self._newline_if_needed()
            self.console.print(Text.assemble(
                ("  └ ", "dim"), (ev.name, "green"),
                (f"({_short(_fmt_args(ev.arguments), 60)})", "dim"),
            ))
        elif ev.kind == "tool_result":
            self.console.print(Text(f"    {_short(ev.result, 84)}", style="dim"))

    def agent_event(self, ev: Event) -> None:
        if ev.kind == "text":
            self.orchestrator_text(ev.text)
            return
        if ev.name == "delegate":  # already shown by delegate()/delegated_event()
            return
        if ev.kind == "tool_call":
            self._newline_if_needed()
            self.console.print(Text.assemble(
                ("⚙ ", "yellow"), (ev.name, "green"),
                (f"({_short(_fmt_args(ev.arguments), 60)})", "dim"),
            ))
        elif ev.kind == "tool_result":
            if ev.name in ("update_plan", "use_skill"):
                for line in ev.result.splitlines():
                    self.console.print(Text(f"  {line}", style="dim"))
            else:
                self.console.print(Text(f"  {_short(ev.result, 84)}", style="dim"))


def _build_orchestrator(model_name: str | None) -> Orchestrator:
    """Build the orchestrator and apply a model (the requested one, the last
    saved one, or the first profile without a key). Raises ``SystemExit`` with a
    message if the model does not exist or the API key is missing."""
    config = AgentConfig()
    apply_workspace(config)
    agent = Orchestrator(
        agents=default_agents(config.workspace), config=config)

    try:
        models = load_models()
    except ValueError as exc:
        raise SystemExit(f"error loading models.json: {exc}")

    if model_name:
        prof = models.get(model_name)
        if prof is None:
            available = ", ".join(models) or "(none)"
            raise SystemExit(
                f"model '{model_name}' is not in models.json. "
                f"Available: {available}")
    else:
        prof = resolve_last_profile(models)
        if prof is None:
            prof = next((p for p in models.values() if not needs_api_key(p)), None)

    if prof is None:
        raise SystemExit(
            "no configured/usable model. Set up conf/models.json "
            "or choose one with --model <name>.")
    if needs_api_key(prof):
        raise SystemExit(
            f"the model '{prof.name}' needs an API key. "
            f"Set {prof.api_key_env or 'the corresponding variable'} in the environment "
            "(e.g. in .env).")

    agent.set_llm(prof.model, prof.base_url, prof.api_key, prof.api,
                  prof.max_output, prof.context_window, prof.temperature,
                  input_cost=prof.input_cost, output_cost=prof.output_cost,
                  thinking_budget=thinking_budget_for(prof))
    return agent


def run(prompt: str, model_name: str | None = None) -> int:
    """Run a single prompt in headless mode. Returns the exit code."""
    console = Console()
    err = Console(stderr=True)

    agent = _build_orchestrator(model_name)

    # Report any MCP servers connected during construction.
    for line in agent.mcp.log_lines:
        err.print(line, style="dim")

    # No interactive terminal: auto-approve edits/command executions.
    set_confirm_hook(lambda action, path: True)

    renderer = _Renderer(console)
    agent.on_delegate = renderer.delegate
    agent.on_event = renderer.delegated_event

    try:
        for ev in agent.events(prompt):
            renderer.agent_event(ev)
    except KeyboardInterrupt:
        err.print("\n⨯ interrupted", style="red")
        return 130
    except Exception as exc:  # noqa: BLE001
        err.print(f"\nerror: {exc}", style="red")
        return 1
    finally:
        set_confirm_hook(None)
        agent.shutdown()  # stop MCP servers

    if not renderer._at_line_start:
        console.print()
    return 0


USAGE = (
    'usage: upcode -p "your prompt"  [--model <name>]\n'
    "       upcode -p -   (read the prompt from stdin)\n"
    '       echo "..." | upcode -p -'
)


def main(argv: list[str]) -> int:
    """Headless mode entry point. ``argv`` are the arguments after the program
    name (without ``argv[0]``).

    The prompt MUST be passed with ``-p``/``--prompt`` (a bare positional string
    is rejected). Use ``-p -`` to read the prompt from stdin."""
    model_name: str | None = None
    prompt: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--help", "-h"):
            print(USAGE)
            return 0
        if arg in ("--model", "-m"):
            if i + 1 >= len(argv):
                print("--model requires a model name", file=sys.stderr)
                return 2
            model_name = argv[i + 1]
            i += 2
            continue
        if arg in ("--prompt", "-p"):
            if i + 1 >= len(argv):
                print("--prompt requires a value (use -p - to read from stdin)",
                      file=sys.stderr)
                return 2
            prompt = argv[i + 1]
            i += 2
            continue
        # Support --prompt=... / -p=... as well.
        if arg.startswith("--prompt=") or arg.startswith("-p="):
            prompt = arg.split("=", 1)[1]
            i += 1
            continue
        positional.append(arg)
        i += 1

    if positional:
        print(f"unexpected argument(s): {' '.join(positional)} — "
              "pass the prompt with -p/--prompt.\n" + USAGE, file=sys.stderr)
        return 2

    if prompt is None:
        print("missing prompt — pass it with -p/--prompt.\n" + USAGE,
              file=sys.stderr)
        return 2

    # "-" (or an empty value with stdin redirected) reads the prompt from stdin.
    if prompt.strip() == "-" or (prompt.strip() == "" and not sys.stdin.isatty()):
        prompt = sys.stdin.read()

    prompt = prompt.strip()
    if not prompt:
        print("empty prompt.\n" + USAGE, file=sys.stderr)
        return 2

    return run(prompt, model_name)
