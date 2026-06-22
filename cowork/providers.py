"""Native LLM providers beyond the OpenAI-compatible layer.

The default path (``CoworkAgent``) talks to any OpenAI-compatible endpoint. This
module adds **native** providers that speak a vendor's own API, unlocking
features the compatibility shim drops.

``anthropic_events`` implements the **Anthropic Messages API** natively (HTTP +
SSE), with tool use and optional extended *thinking*. ``gemini_events`` does the
same for the **Google Gemini API** (``streamGenerateContent``), with function
calling and optional *thinking*. Both use ``httpx`` directly (already a
dependency of ``openai``) — no vendor SDK is required.

The agent keeps its history in the canonical OpenAI-chat shape; this module
converts to/from each vendor's shape on every call, and preserves the raw
content blocks (``_anthropic_blocks`` / ``_gemini_parts``) on assistant messages
so that ``thinking`` blocks (with their signatures) survive multi-turn tool use.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterator
from uuid import uuid4

import httpx

from .agent import Event, TOOL_LIMIT_MESSAGE

# Anthropic API version header and default endpoint.
_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_BASE = "https://api.anthropic.com"
_HTTP_TIMEOUT = httpx.Timeout(300.0, connect=15.0)


# --------------------------------------------------------------------- #
# Multimodal user content
# --------------------------------------------------------------------- #
# A user message's ``content`` is either a plain string or a list of neutral
# blocks (built when the user attaches images in the TUI):
#   {"type": "text",  "text": str}
#   {"type": "image", "media_type": str, "data": <base64>}
# Each provider needs that translated to its own multimodal shape.
def _anthropic_user_content(content):
    """Neutral user content -> Anthropic content (str or list of blocks)."""
    if not isinstance(content, list):
        return content or ""
    out: list[dict] = []
    for b in content:
        if b.get("type") == "image":
            out.append({"type": "image", "source": {
                "type": "base64",
                "media_type": b.get("media_type"),
                "data": b.get("data"),
            }})
        else:
            out.append({"type": "text", "text": b.get("text", "")})
    return out


def _gemini_user_parts(content) -> list[dict]:
    """Neutral user content -> Gemini ``parts``."""
    if not isinstance(content, list):
        return [{"text": content or ""}]
    parts: list[dict] = []
    for b in content:
        if b.get("type") == "image":
            parts.append({"inline_data": {
                "mime_type": b.get("media_type"),
                "data": b.get("data"),
            }})
        else:
            parts.append({"text": b.get("text", "")})
    return parts


def _openai_user_content(content, *, responses: bool):
    """Neutral user content -> OpenAI content (chat or Responses API)."""
    if not isinstance(content, list):
        return content
    out: list[dict] = []
    for b in content:
        if b.get("type") == "image":
            url = f"data:{b.get('media_type')};base64,{b.get('data')}"
            if responses:
                out.append({"type": "input_image", "image_url": url})
            else:
                out.append({"type": "image_url", "image_url": {"url": url}})
        else:
            text = b.get("text", "")
            out.append({"type": "input_text" if responses else "text",
                        "text": text})
    return out


def to_openai_messages(messages: list[dict], *, responses: bool = False) -> list[dict]:
    """Copy ``messages`` translating multimodal user content for OpenAI.

    Most turns have string content and pass through untouched; only user
    messages carrying image blocks are rewritten (a shallow copy of those)."""
    out: list[dict] = []
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            m = {**m, "content": _openai_user_content(m["content"],
                                                       responses=responses)}
        out.append(m)
    return out


# --------------------------------------------------------------------- #
# Conversion: canonical OpenAI-chat  ->  Anthropic
# --------------------------------------------------------------------- #
def to_anthropic_tools(schemas: list[dict]) -> list[dict]:
    """Convert OpenAI tool schemas to the Anthropic ``tools`` shape."""
    out: list[dict] = []
    for s in schemas:
        fn = s.get("function", s)
        out.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def to_anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Convert the canonical history to ``(system, messages)`` for Anthropic.

    - ``system`` messages are concatenated into the top-level system string.
    - consecutive ``tool`` results are grouped into a single ``user`` message
      (Anthropic requires tool_result blocks inside a user turn).
    - assistant messages with ``_anthropic_blocks`` are sent verbatim, preserving
      ``thinking`` signatures required when thinking + tool use are combined."""
    system_parts: list[str] = []
    conv: list[dict] = []
    pending_results: list[dict] = []

    def flush_results() -> None:
        if pending_results:
            conv.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in messages:
        role = m.get("role")
        if role == "system":
            if m.get("content"):
                system_parts.append(m["content"])
            continue
        if role == "tool":
            pending_results.append({
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id"),
                "content": m.get("content") or "",
            })
            continue

        flush_results()
        if role == "user":
            conv.append({"role": "user",
                         "content": _anthropic_user_content(m.get("content"))})
        elif role == "assistant":
            raw = m.get("_anthropic_blocks")
            if raw:
                conv.append({"role": "assistant", "content": raw})
                continue
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc.get("id"),
                               "name": fn.get("name"), "input": args})
            if blocks:  # skip empty assistant turns (Anthropic rejects them)
                conv.append({"role": "assistant", "content": blocks})

    flush_results()
    return "\n\n".join(system_parts), conv


# --------------------------------------------------------------------- #
# SSE stream consumption
# --------------------------------------------------------------------- #
def _consume_sse(lines: Iterator[str], acc: dict) -> Iterator[str]:
    """Parse an Anthropic SSE stream, yielding text chunks as they arrive.

    Side effect: fills ``acc`` with ``usage_in``/``usage_out`` and the ordered
    ``blocks`` (text / tool_use / thinking) for assembly by the caller."""
    blocks: dict[int, dict] = {}
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        etype = data.get("type")

        if etype == "message_start":
            usage = (data.get("message") or {}).get("usage") or {}
            acc["usage_in"] = usage.get("input_tokens", 0) or 0
        elif etype == "content_block_start":
            idx = data["index"]
            cb = data.get("content_block") or {}
            blocks[idx] = {
                "type": cb.get("type"),
                "text": cb.get("text", "") if cb.get("type") == "text" else "",
                "id": cb.get("id"),
                "name": cb.get("name"),
                "partial_json": "",
                "input": cb.get("input") or {},
                "thinking": cb.get("thinking", "") if cb.get("type") == "thinking" else "",
                "signature": cb.get("signature", ""),
            }
            if cb.get("type") == "text" and cb.get("text"):
                yield cb["text"]
        elif etype == "content_block_delta":
            idx = data["index"]
            d = data.get("delta") or {}
            slot = blocks.setdefault(idx, {"type": d.get("type"), "text": "",
                                           "partial_json": "", "thinking": "",
                                           "signature": "", "input": {}})
            dt = d.get("type")
            if dt == "text_delta":
                slot["text"] += d.get("text", "")
                yield d.get("text", "")
            elif dt == "input_json_delta":
                slot["partial_json"] += d.get("partial_json", "")
            elif dt == "thinking_delta":
                slot["thinking"] += d.get("thinking", "")
            elif dt == "signature_delta":
                slot["signature"] += d.get("signature", "")
        elif etype == "message_delta":
            usage = data.get("usage") or {}
            if "output_tokens" in usage:
                acc["usage_out"] = usage.get("output_tokens", 0) or 0
        # content_block_stop / message_stop / ping: nothing to do.

    acc["blocks"] = [blocks[i] for i in sorted(blocks)]


def _assemble(blocks: list[dict]) -> tuple[str, list[dict], list[dict]]:
    """From parsed blocks build ``(text, tool_uses, raw_anthropic_blocks)``.

    ``raw_anthropic_blocks`` is what we resend next turn (keeps thinking
    signatures); ``tool_uses`` is the list of calls to execute."""
    text_parts: list[str] = []
    tool_uses: list[dict] = []
    raw: list[dict] = []
    for b in blocks:
        btype = b.get("type")
        if btype == "text":
            text_parts.append(b.get("text", ""))
            raw.append({"type": "text", "text": b.get("text", "")})
        elif btype == "thinking":
            raw.append({"type": "thinking", "thinking": b.get("thinking", ""),
                        "signature": b.get("signature", "")})
        elif btype == "tool_use":
            pj = b.get("partial_json") or ""
            if pj:
                try:
                    args = json.loads(pj)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = b.get("input") or {}
            tool_uses.append({"id": b.get("id"), "name": b.get("name"), "input": args})
            raw.append({"type": "tool_use", "id": b.get("id"),
                        "name": b.get("name"), "input": args})
    return "".join(text_parts), tool_uses, raw


# --------------------------------------------------------------------- #
# Anthropic native turn
# --------------------------------------------------------------------- #
def _base_url(config) -> str:
    """Normalize the configured base_url to the Anthropic host root.

    Accepts the OpenAI-compat URL (``…/v1`` or ``…/v1/``) and strips it, so the
    same models.json entries work whether ``api`` is ``chat`` or ``anthropic``."""
    base = (config.base_url or _DEFAULT_BASE).rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base or _DEFAULT_BASE


def _build_body(agent, system: str, conv: list[dict]) -> dict[str, Any]:
    cfg = agent.config
    body: dict[str, Any] = {
        "model": cfg.model,
        "max_tokens": cfg.max_output or 4096,
        "messages": conv,
        "stream": True,
    }
    if system:
        body["system"] = system
    if len(agent.tools):
        body["tools"] = to_anthropic_tools(agent.tools.schemas())
    if cfg.thinking_budget and cfg.thinking_budget > 0:
        # Thinking requires max_tokens > budget and an unset/!=fixed temperature.
        budget = cfg.thinking_budget
        if body["max_tokens"] <= budget:
            body["max_tokens"] = budget + 4096
        body["thinking"] = {"type": "enabled", "budget_tokens": budget}
    elif not getattr(agent, "_drop_temperature", False):
        # Some newer models (e.g. Opus 4.8) deprecate `temperature`; we drop it
        # and retry when the API rejects it (see anthropic_events).
        body["temperature"] = cfg.temperature
    return body


def anthropic_events(agent, should_stop: Callable[[], bool] | None = None
                     ) -> Iterator[Event]:
    """Run a turn against the native Anthropic Messages API, emitting Events.

    Mirrors :meth:`CoworkAgent.events`: streams text, runs tool calls, and keeps
    ``agent.messages`` consistent (canonical OpenAI-chat shape, plus the raw
    Anthropic blocks for thinking continuity)."""
    cfg = agent.config
    url = _base_url(cfg) + "/v1/messages"
    headers = {
        "x-api-key": cfg.api_key or "",
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        for _ in range(cfg.max_tool_iterations):
            if should_stop and should_stop():
                return
            system, conv = to_anthropic_messages(agent.messages)
            acc: dict[str, Any] = {"usage_in": 0, "usage_out": 0, "blocks": []}

            # Up to 2 attempts: drop a deprecated `temperature` and retry once.
            for attempt in range(2):
                body = _build_body(agent, system, conv)
                with client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code != 200:
                        resp.read()
                        err = resp.text
                        if (attempt == 0
                                and not getattr(agent, "_drop_temperature", False)
                                and "temperature" in err):
                            agent._drop_temperature = True
                            continue
                        raise RuntimeError(
                            f"Anthropic API {resp.status_code}: {err[:500]}")
                    for chunk in _consume_sse(resp.iter_lines(), acc):
                        if chunk:
                            yield Event("text", text=chunk)
                break

            agent._record_usage(acc["usage_in"], acc["usage_out"])
            text, tool_uses, raw_blocks = _assemble(acc["blocks"])

            if not tool_uses:
                msg: dict[str, Any] = {"role": "assistant", "content": text}
                if raw_blocks:
                    msg["_anthropic_blocks"] = raw_blocks
                agent.messages.append(msg)
                return

            # Assistant turn with tool calls (canonical + raw for continuity).
            agent.messages.append({
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {"id": tu["id"], "type": "function",
                     "function": {"name": tu["name"],
                                  "arguments": json.dumps(tu["input"], ensure_ascii=False)}}
                    for tu in tool_uses
                ],
                "_anthropic_blocks": raw_blocks,
            })
            for tu in tool_uses:
                if should_stop and should_stop():
                    return
                args_json = json.dumps(tu["input"], ensure_ascii=False)
                yield Event("tool_call", name=tu["name"], arguments=args_json)
                result = agent.tools.call(tu["name"], tu["input"])
                agent.messages.append({
                    "role": "tool",
                    "tool_call_id": tu["id"],
                    "content": result,
                })
                yield Event("tool_result", name=tu["name"], result=result)

    yield Event("text", text=TOOL_LIMIT_MESSAGE)


# ===================================================================== #
# Google Gemini (native generateContent API)
# ===================================================================== #
_GEMINI_DEFAULT_BASE = "https://generativelanguage.googleapis.com"

# JSON-Schema keys Gemini's function-declaration schema does not accept.
_GEMINI_SCHEMA_DROP = {
    "$schema", "$ref", "$defs", "definitions", "additionalProperties",
    "title", "default", "examples", "example",
}


def _sanitize_gemini_schema(node: Any) -> Any:
    """Strip JSON-Schema keys Gemini rejects (recursive)."""
    if isinstance(node, dict):
        return {k: _sanitize_gemini_schema(v) for k, v in node.items()
                if k not in _GEMINI_SCHEMA_DROP}
    if isinstance(node, list):
        return [_sanitize_gemini_schema(x) for x in node]
    return node


def to_gemini_tools(schemas: list[dict]) -> list[dict]:
    """Convert OpenAI tool schemas to Gemini ``functionDeclarations`` shape."""
    decls: list[dict] = []
    for s in schemas:
        fn = s.get("function", s)
        decl: dict[str, Any] = {
            "name": fn["name"],
            "description": fn.get("description", ""),
        }
        params = _sanitize_gemini_schema(fn.get("parameters") or {})
        if params.get("properties"):  # Gemini rejects an empty parameter object
            decl["parameters"] = params
        decls.append(decl)
    return [{"functionDeclarations": decls}]


def to_gemini_contents(messages: list[dict]) -> tuple[str, list[dict]]:
    """Convert the canonical history to ``(system, contents)`` for Gemini.

    - ``system`` messages are concatenated into the system instruction.
    - ``tool`` results become ``functionResponse`` parts grouped in a ``user``
      turn (matched to their call's name via the preceding assistant message).
    - assistant messages with ``_gemini_parts`` are sent verbatim, preserving
      ``thoughtSignature`` needed when thinking + tool use are combined."""
    system_parts: list[str] = []
    contents: list[dict] = []
    pending_results: list[dict] = []
    id_to_name: dict[str, str] = {}

    def flush_results() -> None:
        if pending_results:
            contents.append({"role": "user", "content": None,
                             "parts": list(pending_results)})
            pending_results.clear()

    for m in messages:
        role = m.get("role")
        if role == "system":
            if m.get("content"):
                system_parts.append(m["content"])
            continue
        if role == "tool":
            name = id_to_name.get(m.get("tool_call_id")) or m.get("name") or "tool"
            pending_results.append({"functionResponse": {
                "name": name,
                "response": {"result": m.get("content") or ""},
            }})
            continue

        flush_results()
        if role == "user":
            contents.append({"role": "user",
                             "parts": _gemini_user_parts(m.get("content"))})
        elif role == "assistant":
            for tc in m.get("tool_calls") or []:
                id_to_name[tc.get("id")] = tc.get("function", {}).get("name")
            raw = m.get("_gemini_parts")
            if raw:
                contents.append({"role": "model", "parts": raw})
                continue
            parts: list[dict] = []
            if m.get("content"):
                parts.append({"text": m["content"]})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                parts.append({"functionCall": {"name": fn.get("name"), "args": args}})
            if parts:  # skip empty assistant turns
                contents.append({"role": "model", "parts": parts})

    flush_results()
    # `flush_results` stores a transient "content" key for clarity; drop it.
    for c in contents:
        c.pop("content", None)
    return "\n\n".join(system_parts), contents


def _consume_gemini_sse(lines: Iterator[str], acc: dict) -> Iterator[str]:
    """Parse a Gemini SSE stream, yielding visible text chunks as they arrive.

    Side effect: fills ``acc`` with ``usage_in``/``usage_out``, the visible
    ``text``, any ``thoughts`` (text + signature) and the ``func_calls``."""
    text_parts: list[str] = []
    thoughts: list[dict] = []
    func_calls: list[dict] = []
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        usage = data.get("usageMetadata") or {}
        if usage:  # last chunk carries the full totals
            acc["usage_in"] = usage.get("promptTokenCount", 0) or 0
            acc["usage_out"] = usage.get("candidatesTokenCount", 0) or 0
        for cand in data.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                sig = part.get("thoughtSignature")
                if "functionCall" in part:
                    fc = part["functionCall"]
                    func_calls.append({"name": fc.get("name"),
                                       "args": fc.get("args") or {}, "sig": sig})
                elif "text" in part:
                    if part.get("thought"):
                        thoughts.append({"text": part["text"], "sig": sig})
                    else:
                        text_parts.append(part["text"])
                        yield part["text"]

    acc["text"] = "".join(text_parts)
    acc["thoughts"] = thoughts
    acc["func_calls"] = func_calls


def _assemble_gemini_raw(acc: dict) -> list[dict]:
    """Rebuild the model's ``parts`` to resend next turn (keeps thoughtSignature)."""
    raw: list[dict] = []
    for th in acc.get("thoughts", []):
        p: dict[str, Any] = {"text": th["text"], "thought": True}
        if th.get("sig"):
            p["thoughtSignature"] = th["sig"]
        raw.append(p)
    if acc.get("text"):
        raw.append({"text": acc["text"]})
    for fc in acc.get("func_calls", []):
        p = {"functionCall": {"name": fc["name"], "args": fc["args"]}}
        if fc.get("sig"):
            p["thoughtSignature"] = fc["sig"]
        raw.append(p)
    return raw


def _gemini_base_url(config) -> str:
    """Normalize the configured base_url to the Gemini host root.

    Accepts the OpenAI-compat URL (``…/v1beta/openai/``) so the same models.json
    entries work whether ``api`` is ``chat`` or ``gemini``."""
    base = (config.base_url or _GEMINI_DEFAULT_BASE).rstrip("/")
    for suffix in ("/v1beta/openai", "/openai"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base or _GEMINI_DEFAULT_BASE


def _build_gemini_body(agent, system: str, contents: list[dict]) -> dict[str, Any]:
    cfg = agent.config
    gen: dict[str, Any] = {"maxOutputTokens": cfg.max_output or 4096,
                           "temperature": cfg.temperature}
    if cfg.thinking_budget and cfg.thinking_budget > 0:
        gen["thinkingConfig"] = {"thinkingBudget": cfg.thinking_budget,
                                 "includeThoughts": True}
    body: dict[str, Any] = {"contents": contents, "generationConfig": gen}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if len(agent.tools):
        body["tools"] = to_gemini_tools(agent.tools.schemas())
    return body


def gemini_events(agent, should_stop: Callable[[], bool] | None = None
                  ) -> Iterator[Event]:
    """Run a turn against the native Gemini API, emitting Events.

    Mirrors :func:`anthropic_events`: streams text, runs function calls, and
    keeps ``agent.messages`` consistent (canonical OpenAI-chat shape, plus the
    raw Gemini parts for thinking continuity)."""
    cfg = agent.config
    url = (f"{_gemini_base_url(cfg)}/v1beta/models/{cfg.model}"
           ":streamGenerateContent?alt=sse")
    headers = {
        "x-goog-api-key": cfg.api_key or "",
        "content-type": "application/json",
    }

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        for _ in range(cfg.max_tool_iterations):
            if should_stop and should_stop():
                return
            system, contents = to_gemini_contents(agent.messages)
            acc: dict[str, Any] = {"usage_in": 0, "usage_out": 0}
            body = _build_gemini_body(agent, system, contents)

            with client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code != 200:
                    resp.read()
                    raise RuntimeError(
                        f"Gemini API {resp.status_code}: {resp.text[:500]}")
                for chunk in _consume_gemini_sse(resp.iter_lines(), acc):
                    if chunk:
                        yield Event("text", text=chunk)

            agent._record_usage(acc["usage_in"], acc["usage_out"])
            text = acc.get("text", "")
            func_calls = acc.get("func_calls", [])
            raw_parts = _assemble_gemini_raw(acc)

            if not func_calls:
                msg: dict[str, Any] = {"role": "assistant", "content": text}
                if raw_parts:
                    msg["_gemini_parts"] = raw_parts
                agent.messages.append(msg)
                return

            # Synthesize stable ids so the canonical tool/result pairing works.
            tool_calls: list[dict] = []
            for fc in func_calls:
                fc["_id"] = f"gemini_{uuid4().hex[:12]}"
                tool_calls.append({
                    "id": fc["_id"], "type": "function",
                    "function": {"name": fc["name"],
                                 "arguments": json.dumps(fc["args"], ensure_ascii=False)},
                })
            agent.messages.append({
                "role": "assistant",
                "content": text or None,
                "tool_calls": tool_calls,
                "_gemini_parts": raw_parts,
            })
            for fc in func_calls:
                if should_stop and should_stop():
                    return
                args_json = json.dumps(fc["args"], ensure_ascii=False)
                yield Event("tool_call", name=fc["name"], arguments=args_json)
                result = agent.tools.call(fc["name"], fc["args"])
                agent.messages.append({
                    "role": "tool",
                    "tool_call_id": fc["_id"],
                    "content": result,
                })
                yield Event("tool_result", name=fc["name"], result=result)

    yield Event("text", text=TOOL_LIMIT_MESSAGE)
