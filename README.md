> [Versão em Português](README.pt.md)

# Upcode

A Python interface for a **coding agent** built on top of any **OpenAI-compatible
API** — OpenAI, Azure OpenAI, Ollama, LM Studio, vLLM, Groq, etc.

Includes:

- A client over the official `openai` SDK (pointable to any `base_url`).
- Conversation history managed automatically.
- **Function calling** with a schema generated from type annotations.
- Full response (`send`) or **streaming** (`stream`).
- **Coding agent + optional agents**: the agent works directly in the tool
  loop and can delegate to programming-focused agents (`programmer`,
  `architect`, `designer`, `quality`, `pentest`, `devops`, `data`, `qatester`)
  via the `delegate` tool.
- **Agent Skills**: reusable capabilities read from `.skills/` in the
  workspace, loaded on demand with `use_skill`.
- Interactive CLI with `rich`.

## Installation

### One-line install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/upskilling-work/upcode/main/install.sh | bash
```

This will:
1. Check for `git` and Python 3.9+
2. Clone the repo into `~/.upcode`
3. Install all dependencies
4. Create a `.env` from `.env.example`

To install into a custom directory:

```bash
UPCODE_INSTALL_DIR=~/my-upcode curl -fsSL https://raw.githubusercontent.com/upskilling-work/upcode/main/install.sh | bash
```

### Manual install

```bash
git clone https://github.com/upskilling-work/upcode.git
cd upcode
pip install -r requirements.txt
cp .env.example .env   # edit with your endpoint, key and model
```

## Configuration

Set via environment variables (or `.env`):

Upcode uses only **two** of its own variables (plus the provider keys that
`models.json` references via `api_key_env`):

| Variable | Description | Example |
|---|---|---|
| `UPCODE_HOME_DIR` | Base of the `conf/`, `.agents/` and `.skills/` subdirectories | _(default: its own location)_ |
| `UPCODE_WORKSPACE` | Working directory where the agent operates | _(default: the home)_ |
| `UPCODE_MAX_TOOL_ITERATIONS` | Max tool rounds per turn | _(default: `12`)_ |
| `OPENAI_API_KEY` (and similar) | Provider keys, referenced by `models.json` | `sk-...` |

> The model in use is chosen with **`/model`** and is saved in `state.json`
> (in the configuration folder) — the last selection is restored automatically
> on the next run. In the absence of a saved selection, it uses the first usable
> model from `models.json`. The configuration folder is `<UPCODE_HOME_DIR>/conf`
> (default: its own location); it is independent of the current `workspace`.

## Usage

```bash
python -m cowork.tui                   # full-screen TUI (Codex style) ⭐
```

### TUI (`python -m cowork.tui`)

Full-screen (Textual) interface in the Codex style: a scrollable conversation
area, a bordered *composer*, live streaming from the orchestrator and the
agents, a "thinking…" indicator and **Esc to interrupt**.

Keys: `Enter` sends · `Esc` interrupts · `Ctrl+L` clears · `Ctrl+C` quits.
Commands: `/workspace [dir]`, `/status`, `/agents`, `/reset`, `/help`, `/quit`.

`/workspace` with no argument shows the current directory; with a path
(`/workspace ~/project`) it changes the working directory — that's where the
file tools start operating.

`/model` with no argument lists the configured LLMs; with a name
(`/model qwen-coder`) it swaps the model in use (orchestrator and agents).

### Models (`conf/models.json`)

The LLMs selectable with `/model` live in `<UPCODE_HOME_DIR>/conf/models.json`:

```json
{
  "models": [
    { "name": "gemma-local", "label": "Gemma (LM Studio)",
      "model": "google/gemma-4-e2b", "base_url": "http://localhost:1234/v1",
      "api_key": "lm-studio" },
    { "name": "gpt-4o-mini", "label": "OpenAI GPT-4o mini",
      "model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1",
      "api_key_env": "OPENAI_API_KEY" }
  ]
}
```

Each model has `name` (used in `/model`), `model` (id on the API), `base_url` and
the key — literal in `api_key` or by the name of an environment variable in
`api_key_env`. Optional fields: `api` (`"chat"` default or `"responses"` for
codex/GPT-5), `context_window`, `max_output` (becomes the request's `max_tokens`)
and `temperature` (sampling; use a low value for code, e.g. `0.1` for `qwen-coder`).

`context_window` feeds the **context meter**: each turn Upcode estimates usage
and, once it passes 85% of the budget (`context_window − max_output`), warns you
to run **`/compact`** — which asks the LLM itself for a summary of the old turns
and replaces them, keeping the system prompt and the most recent turn. If
`context_window` is **0/absent**, the context is treated as **unlimited** (no
meter or warning). For local models, use the same value as the "Context Length"
loaded in LM Studio.

In the `/model` submenu you can **filter** by typing part of the name or
provider (`claude`, `gpt`, `gemini`, `mini`…); each item shows the context
window (`ctx`) and the max output (`out`).

The example `models.json` already comes **organized by company**, with the main
APIs (all via OpenAI-compatible endpoints) and the local models:

| Company | Key (`.env`) | Examples in `/model` |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `gpt-4.1`, `gpt-4o-mini`, `gpt-5.3-codex` |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-opus`, `claude-sonnet`, `claude-haiku` |
| Google | `GEMINI_API_KEY` | `gemini-pro`, `gemini-flash` |
| xAI | `XAI_API_KEY` | `grok-4`, `grok-3-mini` |
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-chat`, `deepseek-reasoner` |
| Mistral | `MISTRAL_API_KEY` | `mistral-large`, `mistral-small` |
| Groq | `GROQ_API_KEY` | `groq-llama-70b` |
| OpenRouter | `OPENROUTER_API_KEY` | `openrouter-auto` |
| Local | — (LM Studio) | `gemma-local`, `qwen-coder` |

When you select a model whose **key is not available**, `/model` asks how to
provide it: **type the key** on the spot (masked) or **use the suggested
environment variable** (e.g. `XAI_API_KEY`). **Local** models do not ask for a key.

> Claude and Gemini use those providers' **OpenAI-compatible layers** — the
> Anthropic one is a compatibility *shim*, so provider-specific features
> (adaptive thinking, etc.) may not be available through it.

Typing **`/`** brings up a **command menu** that filters as you type; use
`↑`/`↓` to navigate and `Enter` (or click) to select. Selecting **`/model`**
opens a **submenu with the models** from `models.json` to pick one.

The CLI starts in **orchestrator mode**, with **Codex (OpenAI)**-style
interaction — `>_` banner, `›` prompt, "thinking…" indicator and the commands
`/status`, `/agents`, `/reset`, `/help`, `/quit`. The work appears in real time:

```
› what does the code do?

• programmer  List and read the project's files.
  └ list_files(directory='.')
    .env  README.md  cowork/  examples/  requirements.txt
  └ read_file(path='README.md')
    # Upcode — a Python interface for a coding agent…
› The project implements an orchestrator that delegates tasks to agents…
```

Each `•` is an invoked agent; `└` is a tool it called (with the result below, in
gray) and the dimmed text is what it "thinks"/responds. The line starting with
`›` is the orchestrator's final synthesis.

Chat commands: `/quit`, `/reset`, `/agents`, `/help`.

## Usage — orchestrator (library)

```python
from cowork import Orchestrator, default_agents

orchestrator = Orchestrator(agents=default_agents())
print(orchestrator.send(
    "List the project's files and write a summary of what it does."
))
```

### Create an agent (Markdown, Claude Code style)

The recommended way to add an agent is to create a `.md` file in `.agents/` — a
YAML frontmatter with `name`/`description` (and, optionally, `tools` and
`model`) and the *system prompt* in the body:

```markdown
---
name: support
description: Handles and diagnoses technical support tickets.
tools: read_file, search_code, run_command   # optional; omitted = default
model: claude-sonnet                          # optional
---
You are a technical support analyst. Be objective and propose the fix.
```

The app discovers agents in `<UPCODE_HOME_DIR>/.agents` (global library) and in
`<workspace>/.agents` (the project's ones, which take precedence). In the
`tools` field you can use the project names (`read_file`, `write_file`,
`edit_file`, `search_code`, `run_command`, `fetch_url`, …), Claude Code-style
aliases (`Read`, `Write`, `Edit`, `Bash`, `Grep`, `WebFetch`) or `all` for all;
omitted, the agent gets the default set (files + search + internet + skills).
Copy `.agents/_template.md` to get started.

Create an agent in code (API):

```python
from cowork import Orchestrator, Agent, AgentRegistry, ToolRegistry, tool

@tool
def lookup_ticket(id: int) -> str:
    """Look up a support ticket by id."""
    return f"Ticket {id}: customer reports slow login."

reg = ToolRegistry(); reg.add(lookup_ticket)

support = Agent(
    name="support",
    description="Handles and diagnoses support tickets.",
    system_prompt="You are a technical support analyst. Be objective.",
    tools=reg,
)

team = default_agents()
team.add(support)

orchestrator = Orchestrator(agents=team)
print(orchestrator.send("Diagnose ticket 42 and write a reply to the customer."))
```

See [examples/orchestrator.py](examples/orchestrator.py) for a complete example.

## Usage — library

```python
from cowork import CoworkAgent, tool, ToolRegistry

@tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

reg = ToolRegistry()
reg.add(add)

agent = CoworkAgent(tools=reg)
print(agent.send("What is 21 + 21?"))   # the model calls the `add` tool

# streaming
for chunk in agent.stream("Explain the result in one sentence."):
    print(chunk, end="", flush=True)
```

See [examples/custom_tool.py](examples/custom_tool.py) for a complete example.

## Structure

```
cowork/
  agent.py         # CoworkAgent + AgentConfig (tool-calling loop)
  tools.py         # @tool, Tool, ToolRegistry (automatic schema)
  builtin_tools.py # tools (files: read/write/edit/delete_file,
                   #   search_code (grep), run_command (shell),
                   #   calculate, current_time, fetch_url)
  manager.py       # Orchestrator (manager) + Agent (plans and delegates)
  agents.py        # loads the Markdown agents from .agents/
  skills.py        # Agent Skills: discovery in .skills/ + use_skill
  models.py        # loads the LLM profiles from conf/models.json
  tui.py           # full-screen TUI (Textual, Codex style)
.agents/           # 1 .md file per agent (Claude Code style)
  programmer.md  architect.md  designer.md  quality.md  pentest.md  devops.md  data.md  qatester.md
  _template.md     # template to create a new one (files with "_" are ignored)
.skills/            # global Agent Skills library (workspace ones: .upcode/skills/)
conf/
  models.json      # providers/models selectable via /model
  state.json       # last model selection (created/updated at runtime)
examples/
  custom_tool.py
  orchestrator.py
```

### Agents (`.agents/` folder)

Each agent is a `.md` file in `.agents/` (Claude Code style), discovered in
`<UPCODE_HOME_DIR>/.agents` and in `<UPCODE_WORKSPACE>/.agents` (the project's
local one takes precedence). **To create a new one, just add a file** — copy
`.agents/_template.md`:

```markdown
---
name: translator
description: Translates texts between languages.
# tools: optional (omitted = default: files + editing + grep + internet + skills)
# model: optional (overrides the orchestrator's model)
---
You are a professional translator...
```

Files whose name starts with `_` are ignored. The loader injects the default
tools (when `tools` is omitted) and the "act instead of just describing"
reminder. See the frontmatter details in the
[Create an agent](#create-an-agent-markdown-claude-code-style) section.

### Agent Skills

Skills are **reusable capabilities** described in files, discovered in two
sources (merged):

1. **`<UPCODE_HOME_DIR>/.skills/`** — a global/shared library;
2. **`<UPCODE_WORKSPACE>/.upcode/skills/`** — the project's local skills.

On a repeated name, the **workspace-local** skill takes precedence. Each skill is
a folder with a `SKILL.md`:

```
.upcode/skills/
  conventional-commits/
    SKILL.md        # frontmatter (name, description) + instructions
    checklist.md    # (optional) extra files of the skill
```

`SKILL.md` starts with a simple frontmatter and the body with the instructions:

```markdown
---
name: conventional-commits
description: Writes commit messages in the Conventional Commits standard.
---
# instructions
1. ...
```

It works by **progressive disclosure**: at startup, Upcode lists the available
skills (name + description) in the agents' and the orchestrator's prompt; when
the task matches a skill, the agent loads the instructions on demand with the
**`use_skill(<name>)`** tool (and `list_skills` lists them all). The skill's
extra files are read with `read_file` from the indicated paths. The skills follow
the **workspace in use** — switching projects with `/workspace` changes the set
of skills.

### Project rules (`AGENTS.md`)

A **rules file** carries project-specific instructions — conventions, build/test
commands, restrictions — that should steer every turn. Unlike skills (loaded on
demand), rules are **read automatically** and injected into the orchestrator's
and every agent's system prompt, so they always apply. No tool call needed.

Recognized file names, in order of preference per directory: **`AGENTS.md`**,
`UPCODE.md`, `CLAUDE.md`. Discovery, with the most local taking precedence:

1. **`<UPCODE_HOME_DIR>/AGENTS.md`** — a global/shared rules file;
2. from the **git root down to the workspace**, the first recognized file in each
   directory (so a monorepo's root rules apply before a package's). Outside a git
   repo, only the workspace directory is inspected.

```markdown
# my-project
## Build & test
- Python: `pip install -e .` to set up, `pytest` to run tests.
## Conventions
- Always use type hints; never commit secrets.
```

Commands: **`/rules`** lists the rules files in effect; **`/init`** inspects the
repository (stack markers, layout) and writes a ready-to-edit `AGENTS.md`
skeleton, loading it into the prompt immediately. Switching projects with
`/workspace` reloads the rules (and the agents/skills) for the new project.

### MCP servers (`conf/mcp.json`)

Upcode can use tools provided by external **MCP** (Model Context Protocol)
servers — filesystem, git, databases, internal APIs — without hard-coding them.
Define servers in **`<UPCODE_HOME_DIR>/conf/mcp.json`** (global) and/or
**`<workspace>/.upcode/mcp.json`** (project-local, which wins on a name clash),
using the de-facto `mcpServers` shape:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
      "env": { "SOME_TOKEN": "..." },
      "enabled": true
    }
  }
}
```

Each enabled server is started at launch (stdio transport — a local command
speaking JSON-RPC over stdin/stdout) and its tools become available as
**`mcp_<server>_<tool>`**. A server that fails to start is reported and skipped,
never blocking the app. **`/mcp`** lists the connected servers and their tools.
Copy [`conf/mcp.json.example`](conf/mcp.json.example) to get started. The MCP
client uses the standard library only — no extra dependency.

### Native providers & cost

Besides any OpenAI-compatible endpoint, Upcode talks to **Anthropic natively**
(its own Messages API, via `httpx` — no extra SDK). Native mode unlocks
provider-specific features the compatibility shim drops, notably **extended
thinking**. Pick it per model in `models.json` with `"api": "anthropic"` (the
bundled `claude-*` profiles already use it); the `claude-sonnet-thinking` profile
enables thinking via `reasoning_effort: high` (or set `thinking_budget` in tokens).

`models.json` also takes **`input_cost`/`output_cost`** (USD per 1M tokens,
models.dev convention). Upcode tracks **real token usage** returned by the API
(across the orchestrator and all sub-agents), feeds it to the context meter, and
shows the running **cost in `$`** under **`/status`**.
