"""MCP (Model Context Protocol) client — connect to external tool servers.

MCP lets the agent use tools provided by external *servers* (filesystem, git,
databases, internal APIs, …) without hard-coding them. Upcode reads server
definitions from a JSON config, starts each server, lists its tools and exposes
them as regular Upcode :class:`~cowork.tools.Tool` objects registered on demand.

Transport: **stdio** — a local command that speaks JSON-RPC 2.0 over
stdin/stdout, one JSON message per line (the most common MCP transport). The
client is implemented with the standard library only (no extra dependency); the
``mcp`` SDK is not required.

Config (``<UPCODE_HOME_DIR>/conf/mcp.json`` and/or ``<workspace>/.upcode/mcp.json``,
the workspace one taking precedence), using the de-facto ``mcpServers`` shape::

    {
      "mcpServers": {
        "filesystem": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
          "env": {"SOME_TOKEN": "..."},
          "enabled": true
        }
      }
    }

Each server's tools are registered as ``mcp_<server>_<tool>`` so they never clash
with the built-ins or with each other.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field

from .agent import home_dir
from .tools import Tool, ToolRegistry

# MCP protocol version the client advertises in `initialize`.
_PROTOCOL_VERSION = "2024-11-05"

# Default per-request timeout (seconds) waiting for a server response.
_REQUEST_TIMEOUT = 30.0


class MCPError(RuntimeError):
    """An error returned by an MCP server or transport."""


def _sanitize(name: str) -> str:
    """Make a tool name safe for the function-calling API (``[A-Za-z0-9_-]``)."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def _render_content(result: dict | None) -> str:
    """Turn an MCP ``tools/call`` result into plain text for the model.

    The result carries a ``content`` list of blocks (mostly ``{type:"text"}``);
    we join the text blocks and note any non-text block. ``isError`` is surfaced
    as an ``Error:`` prefix so the model can react."""
    if not result:
        return "(no result)"
    blocks = result.get("content") or []
    parts: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        btype = b.get("type")
        if btype == "text":
            parts.append(b.get("text", ""))
        elif btype in ("image", "audio"):
            parts.append(f"[{btype} content omitted]")
        elif btype == "resource":
            res = b.get("resource", {})
            parts.append(res.get("text") or f"[resource {res.get('uri', '')}]")
    text = "\n".join(p for p in parts if p) or "(empty result)"
    if result.get("isError"):
        return "Error: " + text
    return text


@dataclass
class MCPServer:
    """A running MCP server (stdio) and its JSON-RPC client.

    Spawns the configured command and talks JSON-RPC 2.0 over its stdin/stdout
    (newline-delimited). A background reader thread dispatches responses to the
    waiting callers by request id, so concurrent tool calls (parallel agents)
    are safe."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    _proc: subprocess.Popen | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _id: int = 0
    _pending: dict[int, queue.Queue] = field(default_factory=dict, repr=False)
    _alive: bool = False

    # -- lifecycle ----------------------------------------------------- #
    def start(self) -> None:
        """Spawn the process and run the MCP initialize handshake."""
        env = {**os.environ, **{str(k): str(v) for k, v in self.env.items()}}
        self._proc = subprocess.Popen(  # noqa: S603 — user-configured command
            [self.command, *self.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1,
        )
        self._alive = True
        threading.Thread(target=self._read_loop, daemon=True).start()
        self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "Upcode", "version": "0.2"},
        })
        self._notify("notifications/initialized")

    def stop(self) -> None:
        """Terminate the server process (best-effort)."""
        self._alive = False
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            try:
                self._proc.kill()
            except OSError:
                pass
        self._proc = None

    # -- JSON-RPC ------------------------------------------------------ #
    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # skip non-JSON noise
            mid = msg.get("id")
            if mid is not None and mid in self._pending:
                self._pending[mid].put(msg)
        self._alive = False

    def _send(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPError(f"server '{self.name}' is not running")
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()

    def _notify(self, method: str, params: dict | None = None) -> None:
        with self._lock:
            self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _request(self, method: str, params: dict | None = None,
                 timeout: float = _REQUEST_TIMEOUT) -> dict | None:
        with self._lock:
            self._id += 1
            rid = self._id
            reply: queue.Queue = queue.Queue(maxsize=1)
            self._pending[rid] = reply
            self._send({"jsonrpc": "2.0", "id": rid,
                        "method": method, "params": params or {}})
        try:
            msg = reply.get(timeout=timeout)
        except queue.Empty:
            raise MCPError(f"timeout waiting for '{method}' from '{self.name}'")
        finally:
            self._pending.pop(rid, None)
        if "error" in msg:
            err = msg["error"]
            raise MCPError(err.get("message", str(err)) if isinstance(err, dict)
                           else str(err))
        return msg.get("result")

    # -- MCP API ------------------------------------------------------- #
    def list_tools(self) -> list[dict]:
        """Return the server's tool specs (name, description, inputSchema)."""
        result = self._request("tools/list")
        return (result or {}).get("tools", [])

    def call_tool(self, tool: str, arguments: dict) -> str:
        """Call a tool on the server and return its result as text."""
        result = self._request("tools/call",
                               {"name": tool, "arguments": arguments})
        return _render_content(result)


# --------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------- #
def mcp_config_paths(workspace: str | None = None) -> list[str]:
    """Config files to read, in increasing order of precedence.

    1. ``<UPCODE_HOME_DIR>/conf/mcp.json`` — global/shared servers.
    2. ``<workspace>/.upcode/mcp.json`` — the project's servers (win on a clash).
    """
    base = workspace or os.getcwd()
    return [
        os.path.join(home_dir(), "conf", "mcp.json"),
        os.path.join(base, ".upcode", "mcp.json"),
    ]


def load_mcp_config(workspace: str | None = None) -> dict[str, dict]:
    """Merge the ``mcpServers`` maps from the config files (workspace wins).

    Returns ``{server_name: definition}``. Missing/invalid files are ignored.
    Servers with ``"enabled": false`` are dropped."""
    servers: dict[str, dict] = {}
    for path in mcp_config_paths(workspace):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[cowork] warning: could not read {path}: {exc}",
                  file=sys.stderr)
            continue
        for name, defn in (data.get("mcpServers") or {}).items():
            if isinstance(defn, dict):
                servers[name] = defn
    return {n: d for n, d in servers.items() if d.get("enabled", True)}


# --------------------------------------------------------------------- #
# Manager: start servers, expose their tools, shut down
# --------------------------------------------------------------------- #
@dataclass
class MCPManager:
    """Owns the running MCP servers and the Tools wrapping their tools."""

    servers: list[MCPServer] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)
    # server name -> tool names exposed (for /mcp listing)
    catalog: dict[str, list[str]] = field(default_factory=dict)
    # status lines accumulated at startup (shown by the UI after construction).
    log_lines: list[str] = field(default_factory=list)

    def start_all(self, config: dict[str, dict], on_log=None) -> None:
        """Start every configured server and build their Tools.

        Failures (bad command, handshake error) are logged and skipped — they
        never bring down the app. ``on_log(msg)`` receives status lines; they are
        also kept in ``log_lines`` for a UI that connects after construction."""
        def log(msg: str) -> None:
            self.log_lines.append(msg)
            if on_log:
                on_log(msg)

        for name, defn in config.items():
            command = defn.get("command")
            if not command:
                log(f"mcp: server '{name}' has no 'command' — skipped")
                continue
            server = MCPServer(
                name=name,
                command=command,
                args=[str(a) for a in defn.get("args", [])],
                env=defn.get("env", {}) or {},
            )
            try:
                server.start()
                specs = server.list_tools()
            except (MCPError, OSError, FileNotFoundError) as exc:
                log(f"mcp: '{name}' failed to start: {exc}")
                server.stop()
                continue
            names: list[str] = []
            for spec in specs:
                tool = self._wrap(server, spec)
                if tool is not None:
                    self.tools.append(tool)
                    names.append(tool.name)
            self.servers.append(server)
            self.catalog[name] = names
            log(f"mcp: '{name}' connected — {len(names)} tool(s)")

    def _wrap(self, server: MCPServer, spec: dict) -> Tool | None:
        """Wrap one MCP tool spec as an Upcode Tool."""
        raw = spec.get("name")
        if not raw:
            return None
        tool_name = _sanitize(f"mcp_{server.name}_{raw}")[:64]
        description = (spec.get("description")
                       or f"{raw} (via MCP server '{server.name}')")
        schema = spec.get("inputSchema") or {"type": "object", "properties": {}}
        # JSON Schema from MCP is already in the shape Tool.parameters expects.

        def func(_server: MCPServer = server, _raw: str = raw, **kwargs) -> str:
            return _server.call_tool(_raw, kwargs)

        return Tool(func=func, name=tool_name,
                    description=description, parameters=schema)

    def register(self, registry: ToolRegistry) -> None:
        """Add all MCP tools to ``registry`` (idempotent)."""
        for tool in self.tools:
            registry.register(tool)

    def shutdown(self) -> None:
        """Stop every server (best-effort)."""
        for server in self.servers:
            server.stop()
        self.servers.clear()


def connect(workspace: str | None = None, on_log=None) -> MCPManager:
    """Build a manager, start the configured servers and return it.

    With no config it returns an empty manager (zero overhead, no subprocesses)."""
    manager = MCPManager()
    config = load_mcp_config(workspace)
    if config:
        manager.start_all(config, on_log=on_log)
    return manager
