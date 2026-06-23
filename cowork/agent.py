"""Upcode agent over an OpenAI-compatible API.

Keeps the conversation history, exposes tools to the model and resolves the
tool-calling loop automatically. Supports a full or streaming response.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from openai import OpenAI

from .tools import ToolRegistry


def project_root() -> str:
    """Project root — the folder above the ``cowork`` package (its "own location").
    Independent of the current directory."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def home_dir() -> str:
    """Upcode base directory (``UPCODE_HOME_DIR`` or its own location).

    It is the root of the default subdirectories: ``conf/`` (models.json/state.json),
    ``.agents/`` (Markdown agents) and ``.skills/``."""
    return os.path.abspath(os.getenv("UPCODE_HOME_DIR") or project_root())


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token estimate (~4 chars/token) for a list of messages.

    No tokenizer — only a guard-rail for the context meter/warning.
    """
    chars = sum(len(json.dumps(m, ensure_ascii=False, default=str)) for m in messages)
    return chars // 4


@dataclass
class Event:
    """An event emitted during the agent's execution.

    ``kind`` indicates the type:

    - ``"text"``: chunk of text meant for the user (``text``).
    - ``"tool_call"``: the model decided to call a tool (``name``,
      ``arguments`` as raw JSON).
    - ``"tool_result"``: the tool's result (``name``, ``result``).
    """

    kind: str
    text: str = ""
    name: str = ""
    arguments: str = ""
    result: str = ""


DEFAULT_SYSTEM_PROMPT = (
    "You are Upcode, a coding agent. Be direct, helpful and proactive. Use the "
    "available tools when they help, and briefly explain what you did."
)

# Message emitted on reaching `max_tool_iterations`: instead of ending abruptly,
# the agent asks the user how to proceed (configurable via env var).
TOOL_LIMIT_MESSAGE = (
    "\n[Reached the tool-step limit. I paused here without finishing. How should "
    "I proceed — delegate to an agent again, or resolve it myself? "
    "(raise UPCODE_MAX_TOOL_ITERATIONS to allow more steps per turn.)]"
)

@dataclass
class UsageTracker:
    """Accumulates real token usage and cost across the session.

    Shared by the orchestrator and every (sub)agent (via ``AgentConfig``) so the
    reported cost covers the whole turn, not just one agent. Costs use the
    models.dev convention: USD per 1,000,000 tokens."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, input_tokens: int, output_tokens: int,
            input_cost: float | None, output_cost: float | None) -> None:
        self.input_tokens += input_tokens or 0
        self.output_tokens += output_tokens or 0
        if input_cost:
            self.cost_usd += (input_tokens or 0) / 1_000_000 * input_cost
        if output_cost:
            self.cost_usd += (output_tokens or 0) / 1_000_000 * output_cost


@dataclass
class AgentConfig:
    """Agent configuration.

    Model, endpoint and key come from ``models.json`` (profile applied via
    ``set_llm``/``reconfigure``); they stay empty here until a profile is applied."""

    model: str = ""
    base_url: str | None = None
    api_key: str | None = None
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    # Working directory (env UPCODE_WORKSPACE; default: current directory).
    workspace: str = field(
        default_factory=lambda: os.path.abspath(os.getenv("UPCODE_WORKSPACE") or os.getcwd())
    )
    # API used: "chat" (chat/completions), "responses" (codex/GPT-5) or
    # "anthropic" (native Anthropic Messages API). From models.json per profile.
    api: str = "chat"
    # Max output tokens and context window — defined per model in models.json;
    # these are just the initial defaults.
    max_output: int = 4096
    context_window: int = 0
    temperature: float = 0.7
    # Pricing (USD per 1M tokens) from models.json — drives the cost meter.
    input_cost: float | None = None
    output_cost: float | None = None
    # Extended thinking budget (tokens). >0 enables it on the Anthropic provider.
    thinking_budget: int = 0
    # Shared usage/cost accumulator (set by the Orchestrator; shared with agents).
    usage_tracker: UsageTracker | None = None
    max_tool_iterations: int = field(
        default_factory=lambda: int(os.getenv("UPCODE_MAX_TOOL_ITERATIONS", "12")))


def apply_workspace(config: AgentConfig) -> str:
    """Change the process directory to ``config.workspace``.

    This is what makes the ``UPCODE_WORKSPACE`` variable actually take effect:
    file tools resolve relative paths from the current directory. Does nothing if
    the workspace is not a valid directory. Returns the directory in use.
    """
    ws = config.workspace
    if ws and os.path.isdir(ws) and os.path.abspath(ws) != os.getcwd():
        os.chdir(ws)
    return os.getcwd()


class CoworkAgent:
    """A conversational agent with tool support."""

    def __init__(self, config: AgentConfig | None = None,
                 tools: ToolRegistry | None = None) -> None:
        self.config = config or AgentConfig()
        self.tools = tools or ToolRegistry()
        # An empty api_key is accepted by local servers; OpenAI() requires something.
        self.client = OpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key or "not-needed",
        )
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.config.system_prompt}
        ]
        # Real token usage from the API (for the cost/context meter).
        self.session_input = 0
        self.session_output = 0
        # input+output of the last API call ≈ current context size (real tokens).
        self.last_context_tokens = 0
        # Compatibility probes for chat/completions. Newer OpenAI models
        # (gpt-5/o-series) require `max_completion_tokens` instead of `max_tokens`
        # and reject a non-default `temperature`. We auto-adjust on the first 400
        # and remember the choice for the rest of the session.
        self._max_tokens_param = "max_tokens"
        self._drop_temperature = False

    def reconfigure(self, *, model: str, base_url: str | None,
                    api_key: str | None, api: str = "chat",
                    max_output: int | None = None,
                    context_window: int | None = None,
                    temperature: float | None = None,
                    input_cost: float | None = None,
                    output_cost: float | None = None,
                    thinking_budget: int | None = None) -> None:
        """Swap the model/endpoint/key/API and rebuild the client."""
        self.config.model = model
        self.config.base_url = base_url
        self.config.api_key = api_key
        self.config.api = api or "chat"
        if max_output:
            self.config.max_output = max_output
        if context_window is not None:
            self.config.context_window = context_window
        if temperature is not None:
            self.config.temperature = temperature
        # Pricing/thinking are reset on every model switch (None/0 = unset).
        self.config.input_cost = input_cost
        self.config.output_cost = output_cost
        self.config.thinking_budget = thinking_budget or 0
        self._max_tokens_param = "max_tokens"  # re-probed for the new model
        self._drop_temperature = False
        self.client = OpenAI(base_url=base_url, api_key=api_key or "not-needed")

    def _record_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Record real token usage from an API response (cost + context meter)."""
        self.last_context_tokens = (input_tokens or 0) + (output_tokens or 0)
        self.session_input += input_tokens or 0
        self.session_output += output_tokens or 0
        tracker = self.config.usage_tracker
        if tracker is not None:
            tracker.add(input_tokens, output_tokens,
                        self.config.input_cost, self.config.output_cost)

    def cost(self) -> float:
        """Session cost in USD (shared tracker if present, else this agent's)."""
        if self.config.usage_tracker is not None:
            return self.config.usage_tracker.cost_usd
        cost = 0.0
        if self.config.input_cost:
            cost += self.session_input / 1_000_000 * self.config.input_cost
        if self.config.output_cost:
            cost += self.session_output / 1_000_000 * self.config.output_cost
        return cost

    # ------------------------------------------------------------------ #
    # History management
    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        """Clear the history, keeping the system prompt."""
        self.messages = [{"role": "system", "content": self.config.system_prompt}]

    def _request_kwargs(self, stream: bool) -> dict[str, Any]:
        from .providers import to_openai_messages
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": to_openai_messages(self.messages),
            "stream": stream,
        }
        if not self._drop_temperature:
            kwargs["temperature"] = self.config.temperature
        if self.config.max_output:
            kwargs[self._max_tokens_param] = self.config.max_output
        if len(self.tools):
            kwargs["tools"] = self.tools.schemas()
            kwargs["tool_choice"] = "auto"
        if stream:
            # Ask for token usage in the final stream chunk (real cost/context).
            kwargs["stream_options"] = {"include_usage": True}
        return kwargs

    def _chat_create(self, stream: bool):
        """Create a chat/completions request, auto-adjusting unsupported params.

        Newer OpenAI models (gpt-5/o-series) reject ``max_tokens`` (want
        ``max_completion_tokens``) and a non-default ``temperature``. On those
        specific 400s we adjust the request, remember the choice for the rest of
        the session, and retry — so the same models work without per-model config."""
        for _ in range(3):  # at most: fix max_tokens, then temperature
            try:
                return self.client.chat.completions.create(
                    **self._request_kwargs(stream=stream))
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if (self._max_tokens_param == "max_tokens"
                        and "max_completion_tokens" in msg):
                    self._max_tokens_param = "max_completion_tokens"
                    continue
                if (not self._drop_temperature
                        and "temperature" in msg
                        and ("does not support" in msg or "Unsupported" in msg
                             or "unsupported" in msg)):
                    self._drop_temperature = True
                    continue
                raise
        # Final attempt (let any error propagate).
        return self.client.chat.completions.create(**self._request_kwargs(stream=stream))

    # ------------------------------------------------------------------ #
    # Full response
    # ------------------------------------------------------------------ #
    def _heal_dangling_tool_calls(self) -> None:
        """Repair the history before a new request.

        An interrupted turn (Esc) can leave an ``assistant`` message with
        ``tool_calls`` without the corresponding ``tool`` messages — the API
        requires every ``tool_call_id`` to have a response and, without it,
        rejects the request ('tool_calls must be followed by tool messages').
        We insert synthetic responses for the unanswered ids, keeping the
        history valid."""
        responded = {m.get("tool_call_id") for m in self.messages
                     if m.get("role") == "tool"}
        healed: list[dict[str, Any]] = []
        for m in self.messages:
            healed.append(m)
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    cid = tc.get("id")
                    if cid and cid not in responded:
                        healed.append({
                            "role": "tool",
                            "tool_call_id": cid,
                            "content": "[interrupted by the user — no result]",
                        })
                        responded.add(cid)
        self.messages = healed

    def send(self, user_message: str | list[dict]) -> str:
        """Send a user message and return the final response (text).

        Automatically resolves any tool calls.
        """
        if self.config.api in ("responses", "anthropic", "gemini"):
            return "".join(ev.text for ev in self.events(user_message)
                           if ev.kind == "text")

        self._heal_dangling_tool_calls()
        self.messages.append({"role": "user", "content": user_message})

        for _ in range(self.config.max_tool_iterations):
            response = self._chat_create(stream=False)
            usage = getattr(response, "usage", None)
            if usage:
                self._record_usage(getattr(usage, "prompt_tokens", 0) or 0,
                                   getattr(usage, "completion_tokens", 0) or 0)
            msg = response.choices[0].message

            if not msg.tool_calls:
                self.messages.append({"role": "assistant", "content": msg.content or ""})
                return msg.content or ""

            # Record the assistant's call and run each tool.
            self.messages.append(msg.model_dump(exclude_none=True))
            for call in msg.tool_calls:
                result = self.tools.call(call.function.name, call.function.arguments)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                })

        return TOOL_LIMIT_MESSAGE.strip()

    # ------------------------------------------------------------------ #
    # Streaming
    # ------------------------------------------------------------------ #
    def stream(self, user_message: str | list[dict]) -> Iterator[str]:
        """Streaming version of :meth:`send`, yields chunks of text.

        A shortcut over :meth:`events` that emits only the text meant for the
        user (ignores tool calls).
        """
        for ev in self.events(user_message):
            if ev.kind == "text":
                yield ev.text

    def events(self, user_message: str | list[dict],
               should_stop: Callable[[], bool] | None = None) -> Iterator[Event]:
        """Run the turn in streaming mode emitting :class:`Event`.

        Emits the assistant's text in chunks and, each round, one event per tool
        call (before) and its result (after). It is the basis used by the CLI to
        show what each (sub)agent is doing in real time.

        ``should_stop`` (optional): consulted each round and before each tool; if
        it returns ``True``, the loop stops (used to interrupt an agent in
        progress)."""
        self._heal_dangling_tool_calls()
        self.messages.append({"role": "user", "content": user_message})
        if self.config.api == "responses":
            yield from self._events_responses()
            return
        if self.config.api == "anthropic":
            from .providers import anthropic_events
            yield from anthropic_events(self, should_stop=should_stop)
            return
        if self.config.api == "gemini":
            from .providers import gemini_events
            yield from gemini_events(self, should_stop=should_stop)
            return

        for _ in range(self.config.max_tool_iterations):
            if should_stop and should_stop():
                return
            stream = self._chat_create(stream=True)

            content_parts: list[str] = []
            tool_calls: dict[int, dict[str, Any]] = {}

            for chunk in stream:
                # The final usage-only chunk has empty `choices`.
                if getattr(chunk, "usage", None):
                    self._record_usage(getattr(chunk.usage, "prompt_tokens", 0) or 0,
                                       getattr(chunk.usage, "completion_tokens", 0) or 0)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.content:
                    content_parts.append(delta.content)
                    yield Event("text", text=delta.content)

                for tc in delta.tool_calls or []:
                    slot = tool_calls.setdefault(
                        tc.index,
                        {"id": "", "name": "", "arguments": "", "extra": {}},
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments
                    # Preserve provider-specific extras on the tool call (e.g.
                    # Gemini's `extra_content`/thought_signature). They MUST be
                    # echoed back on the next request or the API rejects it
                    # ('Function call is missing a thought_signature'). Gated on
                    # presence, so other providers are unaffected.
                    if getattr(tc, "model_extra", None):
                        slot["extra"].update(tc.model_extra)
                    if tc.function and getattr(tc.function, "model_extra", None):
                        slot["extra"].update(tc.function.model_extra)

            if not tool_calls:
                self.messages.append({"role": "assistant", "content": "".join(content_parts)})
                return

            # Rebuild the assistant message with the tool calls and run them.
            assistant_msg = {
                "role": "assistant",
                "content": "".join(content_parts) or None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                        # Re-attach provider extras (Gemini thought_signature etc.).
                        **c["extra"],
                    }
                    for c in tool_calls.values()
                ],
            }
            self.messages.append(assistant_msg)

            for c in tool_calls.values():
                if should_stop and should_stop():
                    return
                yield Event("tool_call", name=c["name"], arguments=c["arguments"])
                result = self.tools.call(c["name"], c["arguments"])
                # Append the result BEFORE emitting the event: if the consumer
                # interrupts (Esc) on receiving the tool_result, the history is
                # already consistent (tool_call with its response).
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": c["id"],
                    "content": result,
                })
                yield Event("tool_result", name=c["name"], result=result)

        yield Event("text", text=TOOL_LIMIT_MESSAGE)

    # ------------------------------------------------------------------ #
    # Responses API (codex/GPT-5 models)
    # ------------------------------------------------------------------ #
    def _responses_tools(self) -> list[dict[str, Any]]:
        """Convert the tool schemas to the Responses API format.

        In chat/completions the function is nested in ``function``; in the
        Responses API the fields sit at the top level of the ``type: function``
        item.
        """
        out: list[dict[str, Any]] = []
        for s in self.tools.schemas():
            fn = s["function"]
            out.append({
                "type": "function",
                "name": fn["name"],
                "description": fn["description"],
                "parameters": fn["parameters"],
            })
        return out

    def _events_responses(self) -> Iterator[Event]:
        """Version of :meth:`events` for the Responses API (``client.responses``)."""
        for _ in range(self.config.max_tool_iterations):
            from .providers import to_openai_messages
            kwargs: dict[str, Any] = {
                "model": self.config.model,
                "input": to_openai_messages(self.messages, responses=True),
                "stream": True,
            }
            if self.config.max_output:
                kwargs["max_output_tokens"] = self.config.max_output
            if len(self.tools):
                kwargs["tools"] = self._responses_tools()

            stream = self.client.responses.create(**kwargs)

            text_parts: list[str] = []
            calls: list[dict[str, str]] = []

            for event in stream:
                etype = getattr(event, "type", "")
                if etype == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    text_parts.append(delta)
                    yield Event("text", text=delta)
                elif etype == "response.completed":
                    usage = getattr(getattr(event, "response", None), "usage", None)
                    if usage:
                        self._record_usage(getattr(usage, "input_tokens", 0) or 0,
                                           getattr(usage, "output_tokens", 0) or 0)
                elif etype == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if item is not None and getattr(item, "type", "") == "function_call":
                        calls.append({
                            "call_id": item.call_id,
                            "name": item.name,
                            "arguments": item.arguments or "",
                        })

            if not calls:
                self.messages.append({"role": "assistant", "content": "".join(text_parts)})
                return

            # Record the text (if any), the calls and the results.
            if any(text_parts):
                self.messages.append({"role": "assistant", "content": "".join(text_parts)})
            for c in calls:
                self.messages.append({
                    "type": "function_call",
                    "call_id": c["call_id"],
                    "name": c["name"],
                    "arguments": c["arguments"],
                })
                yield Event("tool_call", name=c["name"], arguments=c["arguments"])
                result = self.tools.call(c["name"], c["arguments"])
                yield Event("tool_result", name=c["name"], result=result)
                self.messages.append({
                    "type": "function_call_output",
                    "call_id": c["call_id"],
                    "output": result,
                })

        yield Event("text", text=TOOL_LIMIT_MESSAGE)
