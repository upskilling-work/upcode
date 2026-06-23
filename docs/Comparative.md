> [Versão em Português](Comparative.pt.md)

# Upcode vs. current coding agents

Feature comparison between **Upcode** (this project) and the leading coding
agents on the market: **opencode** (SST), **Claude Code** (Anthropic),
**Antigravity** (Google) and **Codex CLI** (OpenAI).

> Legend: ✅ has it · 🟡 partial · ❌ does not have it

---

## Comparison table

| Feature | Upcode | opencode | Claude Code | Antigravity | Codex CLI |
|---|:--:|:--:|:--:|:--:|:--:|
| **Tool-calling loop** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Response streaming** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **File tools** (read/write/edit/delete) | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Code search** (grep) | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Shell execution** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Web fetch** | ✅ | ✅ | ✅ | ✅ | ✅ (web search) |
| **TODO / task plan** | 🟡 (`update_plan`) | ✅ | ✅ | ✅ (Artifacts) | ✅ |
| **Sub-agents / multi-agent** | ✅ (`delegate`) | ✅ | ✅ (Task/subagents) | ✅ (Agent Manager) | 🟡 (Agents SDK) |
| **Parallel agent execution** | ✅ (`delegate_parallel`) | 🟡 | 🟡 | ✅ (up to 5) | ❌ |
| **Markdown agents** (frontmatter) | ✅ (`.agents/`) | ✅ | ✅ (`.claude/agents`) | ❌ | ❌ |
| **Agent Skills** (`.skills/`) | ✅ | ❌ | ✅ | ❌ | ❌ |
| **Interactive TUI** | ✅ (Textual) | ✅ (Go/Bubbletea) | ✅ | ✅ (IDE/VS Code) | ✅ |
| **Headless / non-interactive mode** | ✅ | ✅ | ✅ (`-p`) | ❌ | ✅ (`exec`) |
| **Custom slash-commands** | ❌ | ✅ | ✅ | ❌ | ✅ (prompts) |
| **`/init` (generates project doc)** | ✅ | ✅ | ✅ | 🟡 | ✅ |
| **Rules/context file** (AGENTS.md etc.) | ✅ | ✅ | ✅ (CLAUDE.md) | ✅ | ✅ (AGENTS.md) |
| **Granular permissions** (allow/ask/deny) | 🟡 (toggle `/auto`) | ✅ | ✅ | ✅ | ✅ (3 modes) |
| **Execution sandbox** | ❌ | 🟡 | 🟡 | ✅ | ✅ |
| **Plan mode (read-only)** | ✅ (`/plan` toggle) | ✅ | ✅ | ✅ | ✅ (read-only) |
| **MCP (Model Context Protocol)** | ✅ (stdio) | ✅ | ✅ | ✅ | ✅ |
| **LSP / diagnostics** | ❌ | ✅ | 🟡 | ✅ | 🟡 |
| **Auto formatters** | ❌ | ✅ | 🟡 (hooks) | ✅ | 🟡 |
| **Persistent sessions / resume** | ✅ (`/sessions`,`/resume`) | ✅ | ✅ | ✅ | ✅ |
| **Session sharing (share link)** | ❌ | ✅ | ❌ | 🟡 | ✅ (cloud) |
| **Snapshots / undo-redo / checkpoints** | 🟡 (`/undo` per turn) | ✅ | ✅ (`/rewind`) | ✅ | ✅ |
| **Context compaction** | ✅ (`/compact`) | ✅ | ✅ (auto) | ✅ | ✅ |
| **Real tokenizer / cost in $** | ✅ (real API tokens) | ✅ | ✅ | ✅ | ✅ |
| **Native providers** (Anthropic, Gemini…) | 🟡 (native Anthropic + OpenAI-compat) | ✅ | ✅ (Anthropic) | ✅ | 🟡 (OpenAI) |
| **Model catalog** (pricing/limits) | 🟡 (`models.json` + cost) | ✅ (models.dev) | ✅ | ✅ | ✅ |
| **Auth / OAuth login** | ❌ (env/key) | ✅ | ✅ | ✅ | ✅ |
| **Multimodal / images** | ❌ | ✅ | ✅ | ✅ | ✅ |
| **`@file` / `@agent` mentions** | ❌ | ✅ | ✅ | ✅ | ✅ |
| **Client/server architecture** | ❌ | ✅ | 🟡 (SDK) | ✅ | ✅ (cloud) |
| **IDE / VS Code integration** | ❌ | ✅ | ✅ | ✅ (is an IDE) | ✅ |
| **GitHub / CI integration** | 🟡 (headless) | ✅ | ✅ (Actions) | 🟡 | ✅ |
| **Browser use / browser testing** | 🟡 (`browser_test`) | ❌ | 🟡 (MCP) | ✅ (native) | ❌ |
| **Themes / customizable keybinds** | ❌ | ✅ | 🟡 | ✅ | 🟡 |

---

## Implementation suggestions for Upcode

> **Already implemented** (see table and Summary): MCP (stdio), native providers
> + real tokens/cost (Anthropic + Gemini), persistent sessions
> (`/sessions`/`/resume`), edit undo (`/undo`), read-only Plan mode
> (`/plan`) and project rules (AGENTS.md + `/init`/`/rules`). These have been
> removed from this list. Below are only the **pending** gaps, ordered by impact.

Each item explains **the feature**, **how to implement it in Upcode** and **what
Upcode already does today** (where something is partial).

### 1. Granular permissions

- **What it is:** per-tool/command rules — allow, ask or deny
  (e.g. `run_command` with `rm` always asks; `read_file` always allows).
- **How to implement:** evolve the `_confirm_hook` in `builtin_tools.py` to
  consult a policy loaded from `settings.json` (allow/ask/deny lists with glob
  per tool and per command pattern). Support three global modes (read-only,
  auto-workspace, full), mirroring Codex. Plan mode (`/plan`) is already, in
  practice, the "read-only" rung — reuse the same `set_read_only`/guard as base.
- **Upcode today:** 🟡 — `set_confirm_hook` + binary toggle `/auto`
  (all or nothing) and confirmation on write/delete/run.

### 2. LSP / diagnostics

- **What it is:** run language servers to give the agent real errors/warnings
  after editing (instead of editing "blind").
- **How to implement:** `cowork/lsp.py` with a minimal LSP client (stdio,
  `pylsp`/`tsserver`), started per language detected in the workspace. After each
  `edit_file`/`write_file`, send `didChange` and return the `diagnostics` as part
  of the tool result, so the model can fix them in the next step.
- **Upcode today:** ❌ — no diagnostics; the agent only learns of errors by
  running tests/linters via `run_command`.

### 3. Custom slash-commands

- **What it is:** user-defined commands (e.g. `/review`, `/commit`) in Markdown
  files, with a parameterizable prompt. (`/init`, mentioned in the original plan,
  was already implemented alongside project rules.)
- **How to implement:** discover `*.md` in `<workspace>/.upcode/commands/`
  (same pattern as `.agents`/`.skills`), each with frontmatter + body (template
  with `$ARGUMENTS`). Merge into the static `COMMANDS` list in `tui.py` and
  execute by injecting the body as a prompt.
- **Upcode today:** ❌ — fixed command list in `COMMANDS` (`tui.py`).

### 4. Auth / OAuth and credential storage

- **What it is:** `upcode auth login` per provider (OAuth for Anthropic, Copilot,
  or key) with credentials saved securely, no `.env` needed.
- **How to implement:** `auth` subcommand in the entrypoint (`upcode`), storing
  tokens in `~/.upcode/auth.json` (permission 600). `models.py` resolves the key
  in this order: auth store → `api_key_env` → typed on the spot.
- **Upcode today:** ❌ — key only via environment variable/`models.json` or typed
  on the spot (`/model`).

### 5. Multimodal / images and `@` mentions

- **What it is:** attach images (screenshots, diagrams) and reference
  files/agents with `@` directly in the composer.
- **How to implement:** in the TUI composer, detect `@path` (autocomplete files
  in the workspace) and `@agent`, expanding to the file content or forcing
  delegation. For images, build a multimodal `content` (list of `image_url`
  parts) — native providers (Anthropic/Gemini) already exist; what's missing is
  assembling the multimodal `content` and the attach UI.
- **Upcode today:** 🟡 — **paste** accepts text or *objects*. `Ctrl+V`
  (all platforms) reads the OS clipboard directly: an image or file path(s)
  become an atomic marker `[Image N]` / `[Document N]` / `[Video N]` /
  `[File N]` in the composer (deleting the marker removes the object); text is
  inserted. macOS native Cmd+V pastes text (the terminal does not forward Cmd,
  and image-only clipboard does not generate a paste event — hence image capture
  is via Ctrl+V). Images become a real multimodal block, converted per provider in
  `cowork/providers.py` (Anthropic `image`, Gemini `inline_data`, OpenAI
  `image_url`/`input_image`); non-image files are passed as paths for the agent
  to open with the tools. Clipboard in `cowork/clipboard.py` (macOS
  `pngpaste`/`osascript`, Linux `wl-paste`/`xclip`, Windows PowerShell). Still
  missing: **`@file` mentions** (autocomplete/expansion); `@agent` already
  delegates.

### 6. Auto formatters

- **What it is:** format the file after editing (black/prettier/gofmt), keeping
  the project's style.
- **How to implement:** after `write_file`/`edit_file`, map extension → format
  command (configurable in `settings.json`) and run via subprocess on the touched
  file. Silent when the formatter is not installed.
- **Upcode today:** ❌.

### 7. Execution sandbox

- **What it is:** isolate `run_command` (no network by default, restricted to the
  workspace) to reduce risk and prompt injection.
- **How to implement:** option to run commands in a per-platform sandbox
  (`sandbox-exec` on macOS, `bwrap`/namespaces on Linux, or container). Integrate
  with the permissions mode (item 1): outside the workspace ⇒ ask for approval.
- **Upcode today:** ❌ — `run_command` runs directly in the shell with `cwd` in
  the workspace, no isolation.

### 8. Missing / imprecise tools

- **What it is:** specific gaps in the tool set.
- **How to implement:**
  - Real `glob` (today the alias `glob`→`list_files` does **not** glob): add a
    tool based on `pathlib.Path.rglob`/`fnmatch`.
  - `read_file` with `offset`/`limit` by line (today only `max_chars`), to read
    large excerpts in chunks.
  - Multi-file `apply_patch` (Codex style), to edit several files in one call.
  - Preview/approval of a **diff by hunk** before writing.
- **Upcode today:** 🟡 — `edit_file` does exact single-string replacement;
  `read_file` truncates by `max_chars`; alias `glob` points to `list_files`.

---

## Bugs

### Images on vision-less providers (DeepSeek)

- **What it is:** pasting an image (`Ctrl+V` → `[Image N]` marker) and sending
  it with a model **without vision support** breaks the request. DeepSeek models
  (`deepseek-chat`/`deepseek-reasoner`) are OpenAI-compatible but **text-only**;
  the multimodal `content` (list with `image_url`) built in `cowork/providers.py`
  is rejected/ignored by the API. This applies to any text-only model served via
  the OpenAI-compat layer, not just DeepSeek.
- **How to fix:** mark in `models.json` which profiles have vision (flag
  `vision: true`) and, on send, check the active model: if it has **no** vision
  and there are image blocks, either (a) **block the paste** with a warning in
  the TUI, or (b) **strip the image blocks** before sending, warning that they
  were discarded (keeping the text/marker). Ideally also disable the `[Image N]`
  marker in the composer when the current model does not support vision.
- **Status:** ❌ open — no capability check; the image is sent to any model and
  the text-only provider (e.g. DeepSeek) fails.

---

## Summary

Upcode already covers the **core of an agent** well (tool loop, Markdown
sub-agents, Skills, TUI, headless, compaction) and has closed many gaps in
**integration and UX**: **MCP** (stdio), **native Anthropic provider** with
**real tokens/cost**, **project rules** (AGENTS.md + `/init`/`/rules`),
**persistent sessions** (`/sessions`/`/resume`), **edit undo** (`/undo`) and
**read-only Plan mode** (`/plan` toggle) — plus differentiators over some
competitors (Agent Skills, explicit parallel delegation). The gaps that still set
opencode, Claude Code, Antigravity and Codex apart are in **infrastructure**:
auth/OAuth, granular permissions, LSP, custom slash-commands, multimodal/`@`
mentions, sandbox and auto formatters.

---

### Sources

- [opencode — Docs](https://opencode.ai/docs/)
- [Build with Google Antigravity — Google Developers Blog](https://developers.googleblog.com/build-with-google-antigravity-our-new-agentic-development-platform/)
- [Features — Codex CLI | OpenAI Developers](https://developers.openai.com/codex/cli/features)
- [Agent approvals & security — Codex | OpenAI Developers](https://developers.openai.com/codex/agent-approvals-security)
