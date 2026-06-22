"""Orchestrator with specialist agents.

On startup, Upcode acts as an **orchestrator**: it does not solve the task on
its own, but splits it up and delegates each part to an **agent** — an agent
with its own persona and tools. The orchestrator collects the results and
synthesizes the final response to the user.

Each agent runs in isolation (its own history, no shared state) through the
``delegate`` tool, which is injected automatically into the orchestrator.
"""

from __future__ import annotations

import dataclasses
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterator

from .agent import AgentConfig, CoworkAgent, Event, UsageTracker, estimate_tokens
from .tools import Tool, ToolRegistry
from .builtin_tools import (
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
from .builtin_tools import set_read_only
from .skills import list_skills, use_skill
from .rules import rules_prompt

# Headroom reserved on top of max_output when computing the context budget.
_CONTEXT_MARGIN = 512

# Read-only ("plan") mode: the tools the orchestrator may keep — investigation
# and planning only, no writing/executing. Mutating tools are also blocked
# process-wide via set_read_only (covers delegated agents and MCP tools).
_READ_ONLY_TOOLS = {"read_file", "list_files", "search_code", "fetch_url",
                    "update_plan", "list_skills", "use_skill"}

_PLAN_MODE_PREAMBLE = (
    "PLAN MODE (READ-ONLY) IS ACTIVE.\n"
    "You may ONLY investigate (read_file, list_files, search_code, fetch_url) "
    "and record a plan with update_plan. Creating, editing or deleting files and "
    "running commands are DISABLED and will refuse — do NOT claim such work as "
    "done. Produce a concrete, ordered plan of the changes you WOULD make "
    "(which files/functions and which commands), then tell the user to run "
    "/plan again to turn off plan mode and execute.\n"
)

# Coding agent tools (main loop): repository exploration and direct editing,
# command execution, TODO planning and Agent Skills. They coexist with
# `delegate`, used optionally to hand a specific subtask to an agent.
_AGENT_TOOLS: tuple[Tool, ...] = (
    update_plan,
    read_file, list_files, search_code,
    write_file, edit_file, delete_file,
    run_command, fetch_url,
    list_skills, use_skill,
)


@dataclass
class Agent:
    """A specialist agent that the orchestrator can invoke.

    Attributes:
        name: short identifier used in delegation (e.g. ``"programmer"``).
        description: what it is for — the orchestrator reads this to decide
            when to delegate to this agent.
        system_prompt: the agent's persona/instructions.
        tools: tools available to this agent.
        model: specific model (optional; inherits the orchestrator's).
    """

    name: str
    description: str
    system_prompt: str
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    model: str | None = None

    def run(
        self,
        task: str,
        base_config: AgentConfig,
        observer: Callable[[Event], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> str:
        """Run the task as a fresh agent and return the response.

        If ``observer`` is provided, it receives each :class:`Event` from the
        agent in real time (text, tool calls and results) — this is what lets
        the CLI show the agent "thinking" and using tools. ``should_stop`` allows
        interrupting the running agent (Esc)."""
        config = dataclasses.replace(
            base_config,
            system_prompt=self.system_prompt,
            model=self.model or base_config.model,
        )
        agent = CoworkAgent(config=config, tools=self.tools)
        parts: list[str] = []
        for ev in agent.events(task, should_stop=should_stop):
            if observer:
                observer(ev)
            if ev.kind == "text":
                parts.append(ev.text)
        text = "".join(parts).strip()
        if should_stop and should_stop():
            return (text + "\n[interrupted by the user]").strip()
        return text


@dataclass
class AgentRegistry:
    """Collection of agents indexed by name."""

    _by_name: dict[str, Agent] = field(default_factory=dict)

    def add(self, *agents: Agent) -> None:
        for s in agents:
            self._by_name[s.name] = s

    def names(self) -> list[str]:
        return list(self._by_name.keys())

    def __iter__(self):
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def run(
        self,
        name: str,
        task: str,
        base_config: AgentConfig,
        observer: Callable[[Event], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> str:
        if name not in self._by_name:
            available = ", ".join(self.names()) or "(none)"
            return f"Error: agent '{name}' does not exist. Available: {available}."
        return self._by_name[name].run(
            task, base_config, observer=observer, should_stop=should_stop)


# Manager (router) persona. The agent and skill lists are appended dynamically.
ORCHESTRATOR_SYSTEM_PROMPT = (
    "You are the Upcode Manager, working INSIDE a software project in the current "
    "directory. You do NOT do the work yourself: you coordinate a team of "
    "programming agents and a set of ready-made Agent Skills, and you ROUTE "
    "each task to the right place.\n\n"
    "ROUTING — for every request, decide where the work goes:\n"
    "1. PLAN: for a task with more than ~2 steps, call `update_plan` with a short "
    "ordered list of steps, and update it as you progress (advancing "
    "`current_step`). Skip the plan for a single trivial step.\n"
    "2. For each step, choose in THIS order:\n"
    "   a) READY SKILL — if a Skill listed below matches the step, prefer it over "
    "improvising. If the skill is TRIVIAL (a single quick command/lookup, e.g. "
    "`buscar-cep`), run it yourself with `use_skill(<name>)` and follow its "
    "instructions. If the skill is HEAVY (multi-step instructions like writing "
    "tests or building a Dockerfile), do NOT load it into your own context: "
    "delegate the step to the best-fitting agent and tell it which skill to "
    "use — the agents can read and run skills themselves.\n"
    "   b) AGENT — otherwise, if the step falls within ANY agent's "
    "domain (listed below), you MUST hand it off with `delegate`. Do not do it "
    "yourself. Pick the best-fitting agent and give a clear, self-contained "
    "instruction of what to produce (including: actually create/edit the files "
    "with the tools, not just describe them). You may delegate several times.\n"
    "   When you have MULTIPLE INDEPENDENT subtasks (they don't depend on each "
    "other's output and don't edit the same files), delegate them in ONE call to "
    "`delegate_parallel` so the agents run CONCURRENTLY. For ordered/dependent "
    "steps, use `delegate` so each finishes before the next begins.\n"
    "   c) ANSWER DIRECTLY — only when the request is a simple question or "
    "command that NO agent's specialty and NO skill covers (e.g. a greeting, "
    "a definition, the current time). This is the exception, not the rule.\n"
    "3. Collect the results, integrate them, and reply to the user, confirming "
    "what was done (which files were created/changed/deleted).\n\n"
    "IMPORTANT RULES:\n"
    "- DELEGATE BY DEFAULT. The agents cover programming, architecture, "
    "frontend/design, QA/testing, security (pentest), devops and data — so almost "
    "any real task fits one of them. When unsure whether a agent fits, "
    "delegate rather than answering yourself.\n"
    "- IF A DELEGATION DOES NOT FULLY RESOLVE the step (the agent failed, "
    "returned something incomplete, or hit its step limit), do NOT silently retry "
    "or give up: ASK the user how to proceed — delegate to a agent again "
    "(same or different one) or have you resolve it directly — and wait for their "
    "choice.\n"
    "- NEVER end your turn with only a statement of intent (e.g. 'I'll delegate "
    "this', 'let me check'). Whenever you say you will do something, make the "
    "`delegate`/`use_skill` call in the SAME response. Act first, then report — "
    "do not stop after announcing.\n"
    "- When you delegate, instruct the agent to ACTUALLY use the tools to "
    "create/change files and run commands — never to just print code. If a tool "
    "or agent reports the work was CANCELLED by the user, report that "
    "honestly; do NOT claim it succeeded.\n"
    "- Be efficient: route only the necessary steps and avoid redundant "
    "delegations."
)


class Orchestrator:
    """Router manager: plans and routes each step to a ready-made skill or to an
    agent; only answers directly what has neither a specialty nor a skill."""

    def __init__(
        self,
        agents: AgentRegistry,
        config: AgentConfig | None = None,
        on_delegate: Callable[[str, str], None] | None = None,
        on_event: Callable[[str, Event], None] | None = None,
    ) -> None:
        self.agents = agents
        self.base_config = config or AgentConfig()
        # Shared usage/cost tracker: the orchestrator and every (sub)agent point
        # at the same accumulator (inherited via base_config), so the reported
        # cost covers the whole turn.
        self.usage = self.base_config.usage_tracker or UsageTracker()
        self.base_config.usage_tracker = self.usage
        # on_delegate(agent, task): called when a delegation starts.
        self.on_delegate = on_delegate
        # on_event(agent, event): each event from the agent in real time.
        self.on_event = on_event
        # should_stop(): consulted to interrupt a running agent.
        self.should_stop: Callable[[], bool] | None = None
        # Whether `delegate_parallel` actually runs agents concurrently. Default
        # off: it falls back to sequential execution (same result, one agent at
        # a time). Enable concurrency from the UI with `/parallel`.
        self.parallel = False
        # Read-only "plan" mode (item 6): when on, only read/plan tools are
        # exposed and the mutating tools refuse process-wide (set via set_plan_mode).
        self.plan_mode = False
        # Serializes on_delegate/on_event callbacks when several agents stream
        # concurrently (parallel delegation), so observers aren't called from
        # multiple threads at once.
        self._dispatch_lock = threading.Lock()
        # on_log(msg): optional sink for status lines (e.g. MCP connection).
        self.on_log: Callable[[str], None] | None = None

        # MCP servers (Model Context Protocol): external tool providers. Started
        # once here (no-op when there is no mcp.json); their tools are added to
        # the orchestrator's registry on every (re)build.
        from .mcp import connect as _connect_mcp
        self.mcp = _connect_mcp(
            self.base_config.workspace,
            on_log=lambda m: self.on_log and self.on_log(m),
        )

        # Coding agent: drives the main loop with the exploration/editing/
        # execution tools + planning + skills, and keeps `delegate` (sequential)
        # and `delegate_parallel` (concurrent) as OPTIONAL resources to invoke
        # one or several agents.
        orchestrator_config = dataclasses.replace(
            self.base_config,
            system_prompt=self._build_system_prompt(),
        )
        self.agent = CoworkAgent(config=orchestrator_config, tools=self._build_tools())

    # ------------------------------------------------------------------ #
    # Orchestrator construction
    # ------------------------------------------------------------------ #
    def _build_tools(self) -> ToolRegistry:
        """Registry for the orchestrator: coding-agent tools + delegation + MCP.

        In plan mode only the read/plan tools are exposed and external MCP tools
        are withheld (they may mutate state)."""
        reg = ToolRegistry()
        tools = _AGENT_TOOLS
        if self.plan_mode:
            tools = tuple(t for t in _AGENT_TOOLS if t.name in _READ_ONLY_TOOLS)
        reg.add(*tools)
        reg.register(self._delegate_tool())
        reg.register(self._delegate_parallel_tool())
        # External MCP tools (if any server is configured/connected).
        if not self.plan_mode:
            self.mcp.register(reg)
        return reg

    def set_plan_mode(self, enabled: bool) -> None:
        """Enter/leave read-only plan mode (item 6).

        Rebuilds the orchestrator's tools (read/plan only when on), refreshes the
        system prompt in place (keeping the conversation), and flips the
        process-wide read-only guard so delegated agents are blocked too."""
        self.plan_mode = bool(enabled)
        set_read_only(self.plan_mode)
        self.agent.tools = self._build_tools()
        self.apply_system_prompt(reset=False)

    def shutdown(self) -> None:
        """Stop external resources (MCP servers). Safe to call more than once."""
        if getattr(self, "mcp", None) is not None:
            self.mcp.shutdown()

    def _build_system_prompt(self) -> str:
        lines = [ORCHESTRATOR_SYSTEM_PROMPT, "", "Available agents:"]
        for s in self.agents:
            tool_names = ", ".join(t["function"]["name"] for t in s.tools.schemas())
            lines.append(
                f"- {s.name}: {s.description}"
                + (f" (tools: {tool_names})" if tool_names else "")
            )
        # List the workspace's Agent Skills so the manager can load and run them
        # (`use_skill`) when a plan step matches.
        from .skills import load_skills
        skills = load_skills()
        if skills:
            lines.append("")
            lines.append("Available skills (load with `use_skill(<name>)` and "
                         "execute the instructions when a step matches the description):")
            lines += [f"- {s.name}: {s.description}" for s in skills.values()]
        # Project rules (AGENTS.md/UPCODE.md) are read automatically and appended
        # so the orchestrator follows the project's conventions and commands.
        prompt = "\n".join(lines) + rules_prompt()
        if self.plan_mode:
            prompt = _PLAN_MODE_PREAMBLE + "\n" + prompt
        return prompt

    # ------------------------------------------------------------------ #
    # Reload (e.g. after /workspace or /init)
    # ------------------------------------------------------------------ #
    def apply_system_prompt(self, *, reset: bool) -> None:
        """Rebuild the orchestrator's system prompt (agents + skills + rules).

        With ``reset=True`` the conversation is cleared (used when switching
        workspace). With ``reset=False`` only the system message is refreshed in
        place, preserving the current history (used by ``/init`` so newly created
        rules take effect without losing the conversation)."""
        prompt = self._build_system_prompt()
        self.agent.config.system_prompt = prompt
        if reset:
            self.agent.reset()
        elif self.agent.messages:
            self.agent.messages[0] = {"role": "system", "content": prompt}

    def reload_agents(self, agents: AgentRegistry, *, reset: bool = True) -> None:
        """Swap the agent roster and rebuild the prompt + delegation tools.

        Used when the workspace changes (new project = new .agents/.skills/rules)
        or when rules are (re)generated. ``reset`` clears the conversation."""
        self.agents = agents
        self.agent.tools = self._build_tools()
        self.apply_system_prompt(reset=reset)

    def _delegate_tool(self) -> Tool:
        names = self.agents.names()

        def delegate(agent: str, task: str) -> str:
            return self._run_one(agent, task)

        return Tool(
            func=delegate,
            name="delegate",
            description=(
                "Delegate a task to a specialist agent and return its result. "
                "Choose the agent best suited to that part of the task."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "enum": names,
                        "description": "Name of the agent that should perform the task.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Complete, self-contained description of the task for the agent.",
                    },
                },
                "required": ["agent", "task"],
            },
        )

    def _run_one(self, agent: str, task: str) -> str:
        """Run a single delegation, dispatching observer callbacks under a lock."""
        if self.on_delegate:
            with self._dispatch_lock:
                self.on_delegate(agent, task)
        observer = None
        if self.on_event:
            def observer(ev: Event, _agent: str = agent) -> None:
                with self._dispatch_lock:
                    self.on_event(_agent, ev)
        return self.agents.run(
            agent, task, self.base_config, observer=observer,
            should_stop=self.should_stop,
        )

    def _delegate_parallel_tool(self) -> Tool:
        names = self.agents.names()

        def delegate_parallel(tasks: list[dict]) -> str:
            if not tasks:
                return "Error: no tasks provided."
            # Launch every (agent, task) on its own thread and wait for all.
            # Use for INDEPENDENT subtasks only — they share the same workspace,
            # so concurrent edits to the same files can conflict.
            pairs = [(str(t.get("agent", "")).strip(),
                      str(t.get("task", "")).strip()) for t in tasks]
            results: list[tuple[str, str]] = []
            if not self.parallel or len(pairs) == 1:
                # Parallel execution disabled (/parallel off): run sequentially.
                for agent, task in pairs:
                    try:
                        results.append((agent, self._run_one(agent, task)))
                    except Exception as exc:  # noqa: BLE001 — report, don't crash
                        results.append((agent, f"[failed: {exc}]"))
            else:
                with ThreadPoolExecutor(max_workers=min(len(pairs), 8)) as ex:
                    futures = [ex.submit(self._run_one, agent, task)
                               for agent, task in pairs]
                    # Preserve input order in the combined result for readability.
                    for (agent, _task), fut in zip(pairs, futures):
                        try:
                            results.append((agent, fut.result()))
                        except Exception as exc:  # noqa: BLE001 — report, don't crash
                            results.append((agent, f"[failed: {exc}]"))
            return "\n\n".join(f"### {agent}\n{out}" for agent, out in results)

        return Tool(
            func=delegate_parallel,
            name="delegate_parallel",
            description=(
                "Delegate several INDEPENDENT tasks to specialist agents and run "
                "them CONCURRENTLY, returning all results together. Use when the "
                "subtasks do not depend on each other (e.g. one agent writes tests "
                "while another writes docs). For dependent/ordered steps, use "
                "`delegate` instead. Avoid concurrent edits to the same files."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "List of independent delegations to run in parallel.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "agent": {
                                    "type": "string",
                                    "enum": names,
                                    "description": "Name of the agent that should perform the task.",
                                },
                                "task": {
                                    "type": "string",
                                    "description": "Complete, self-contained description of the task.",
                                },
                            },
                            "required": ["agent", "task"],
                        },
                    },
                },
                "required": ["tasks"],
            },
        )

    # ------------------------------------------------------------------ #
    # Interface (mirrors CoworkAgent)
    # ------------------------------------------------------------------ #
    def send(self, user_message: str) -> str:
        return self.agent.send(user_message)

    def stream(self, user_message: str):
        return self.agent.stream(user_message)

    def events(self, user_message: str) -> Iterator[Event]:
        """Events from the orchestrator itself (text and `delegate` calls).

        The agents' events arrive via ``on_event``, not here.
        """
        return self.agent.events(user_message)

    def reset(self) -> None:
        self.agent.reset()

    def set_llm(self, model: str, base_url: str | None, api_key: str | None,
                api: str = "chat", max_output: int | None = None,
                context_window: int | None = None,
                temperature: float | None = None,
                input_cost: float | None = None,
                output_cost: float | None = None,
                thinking_budget: int | None = None) -> None:
        """Swap the LLM in use. Applies to the orchestrator and to the agents
        (which inherit from ``base_config`` when created)."""
        self.base_config.model = model
        self.base_config.base_url = base_url
        self.base_config.api_key = api_key
        self.base_config.api = api or "chat"
        if max_output:
            self.base_config.max_output = max_output
        if context_window is not None:
            self.base_config.context_window = context_window
        if temperature is not None:
            self.base_config.temperature = temperature
        # Pricing/thinking are per-model (reset on each switch).
        self.base_config.input_cost = input_cost
        self.base_config.output_cost = output_cost
        self.base_config.thinking_budget = thinking_budget or 0
        self.agent.reconfigure(
            model=model, base_url=base_url, api_key=api_key, api=api,
            max_output=self.base_config.max_output,
            context_window=self.base_config.context_window,
            temperature=self.base_config.temperature,
            input_cost=input_cost, output_cost=output_cost,
            thinking_budget=thinking_budget or 0,
        )
        # Clear the history: messages from the previous model may contain
        # `tool_calls`/`tool` roles that the new model (e.g. local ones without
        # function-calling) rejects. Without this, the next message errors out
        # until a restart. Reset to start clean with the chosen model.
        self.agent.reset()

    # ------------------------------------------------------------------ #
    # Context: meter + compaction
    # ------------------------------------------------------------------ #
    def context_status(self) -> tuple[int, int]:
        """Return (tokens_used, budget). Budget 0 = unlimited (``context_window``
        missing/zero in models.json).

        Uses the real token count from the last API response when available,
        falling back to the rough char-based estimate before the first call."""
        cfg = self.agent.config
        used = self.agent.last_context_tokens or estimate_tokens(self.agent.messages)
        if not cfg.context_window:
            return used, 0
        budget = max(cfg.context_window - cfg.max_output - _CONTEXT_MARGIN, 1)
        return used, budget

    def cost(self) -> float:
        """Total session cost in USD (orchestrator + all agents)."""
        return self.usage.cost_usd

    def compact(self) -> str:
        """Summarize the old turns with the LLM itself and replace them with a
        single summary, keeping the system prompt and the user's last turn.

        Returns the summary text (or a status message if there is nothing to
        compact)."""
        msgs = self.agent.messages
        # index of the last 'user' — everything before it (except system) is compacted.
        last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"),
                        default=0)
        if last_user <= 1:
            return ""  # nothing to compact (short conversation)

        system, older, recent = msgs[0], msgs[1:last_user], msgs[last_user:]
        summary = self._summarize(older)
        self.agent.messages = [
            system,
            {"role": "user", "content": "Summary of the conversation so far:\n" + summary},
        ] + recent
        return summary

    def _summarize(self, messages: list[dict]) -> str:
        """Ask the current model for a faithful summary of the given messages."""
        cfg = dataclasses.replace(
            self.base_config,
            system_prompt="You summarize technical conversations faithfully and concisely.",
        )
        tmp = CoworkAgent(config=cfg)  # agent without tools
        text = "\n".join(self._render_msg(m) for m in messages)
        prompt = (
            "Summarize the following conversation in short bullet points, preserving "
            "facts, decisions, file names and important open items:\n\n" + text
        )
        try:
            return tmp.send(prompt).strip() or "(empty summary)"
        except Exception as exc:  # noqa: BLE001
            return f"(failed to summarize: {exc})"

    @staticmethod
    def _render_msg(m: dict) -> str:
        role = m.get("role") or m.get("type") or "?"
        body = m.get("content") or m.get("arguments") or m.get("output") or ""
        if not isinstance(body, str):
            import json as _json
            body = _json.dumps(body, ensure_ascii=False, default=str)
        return f"[{role}] {body}"

    @property
    def messages(self) -> list[dict]:
        """The orchestrator's conversation history (for sessions/persistence)."""
        return self.agent.messages

    @messages.setter
    def messages(self, value: list[dict]) -> None:
        self.agent.messages = value

    @property
    def config(self) -> AgentConfig:
        return self.agent.config

    @property
    def tools(self) -> ToolRegistry:
        return self.agent.tools
