# Upcode vs. agentes de código atuais

Comparativo de funcionalidades entre **Upcode** (este projeto) e os principais
agentes de código de mercado: **opencode** (SST), **Claude Code** (Anthropic),
**Antigravity** (Google) e **Codex CLI** (OpenAI).

> Legenda: ✅ tem · 🟡 parcial · ❌ não tem

---

## Tabela comparativa

| Funcionalidade | Upcode | opencode | Claude Code | Antigravity | Codex CLI |
|---|:--:|:--:|:--:|:--:|:--:|
| **Loop de tool-calling** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Streaming de resposta** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Ferramentas de arquivo** (read/write/edit/delete) | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Busca em código** (grep) | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Execução de shell** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Web fetch** | ✅ | ✅ | ✅ | ✅ | ✅ (web search) |
| **TODO / plano de tarefas** | 🟡 (`update_plan`) | ✅ | ✅ | ✅ (Artifacts) | ✅ |
| **Subagentes / multiagente** | ✅ (`delegate`) | ✅ | ✅ (Task/subagents) | ✅ (Agent Manager) | 🟡 (Agents SDK) |
| **Execução paralela de agentes** | ✅ (`delegate_parallel`) | 🟡 | 🟡 | ✅ (até 5) | ❌ |
| **Agentes em Markdown** (frontmatter) | ✅ (`.agents/`) | ✅ | ✅ (`.claude/agents`) | ❌ | ❌ |
| **Agent Skills** (`.skills/`) | ✅ | ❌ | ✅ | ❌ | ❌ |
| **TUI interativa** | ✅ (Textual) | ✅ (Go/Bubbletea) | ✅ | ✅ (IDE/VS Code) | ✅ |
| **Modo headless / não interativo** | ✅ | ✅ | ✅ (`-p`) | ❌ | ✅ (`exec`) |
| **Slash-commands customizados** | ❌ | ✅ | ✅ | ❌ | ✅ (prompts) |
| **`/init` (gera doc do projeto)** | ✅ | ✅ | ✅ | 🟡 | ✅ |
| **Arquivo de regras/contexto** (AGENTS.md etc.) | ✅ | ✅ | ✅ (CLAUDE.md) | ✅ | ✅ (AGENTS.md) |
| **Permissões granulares** (allow/ask/deny) | 🟡 (toggle `/auto`) | ✅ | ✅ | ✅ | ✅ (3 modos) |
| **Sandbox de execução** | ❌ | 🟡 | 🟡 | ✅ | ✅ |
| **Modo Plan (read-only)** | ✅ (`/plan` toggle) | ✅ | ✅ | ✅ | ✅ (read-only) |
| **MCP (Model Context Protocol)** | ✅ (stdio) | ✅ | ✅ | ✅ | ✅ |
| **LSP / diagnósticos** | ❌ | ✅ | 🟡 | ✅ | 🟡 |
| **Formatters automáticos** | ❌ | ✅ | 🟡 (hooks) | ✅ | 🟡 |
| **Sessões persistentes / resume** | ✅ (`/sessions`,`/resume`) | ✅ | ✅ | ✅ | ✅ |
| **Compartilhar sessão (share link)** | ❌ | ✅ | ❌ | 🟡 | ✅ (cloud) |
| **Snapshots / undo-redo / checkpoints** | 🟡 (`/undo` por turno) | ✅ | ✅ (`/rewind`) | ✅ | ✅ |
| **Compactação de contexto** | ✅ (`/compact`) | ✅ | ✅ (auto) | ✅ | ✅ |
| **Tokenizer real / custo em $** | ✅ (tokens reais da API) | ✅ | ✅ | ✅ | ✅ |
| **Providers nativos** (Anthropic, Gemini…) | 🟡 (Anthropic nativo + OpenAI-compat) | ✅ | ✅ (Anthropic) | ✅ | 🟡 (OpenAI) |
| **Catálogo de modelos** (pricing/limites) | 🟡 (`models.json` + custo) | ✅ (models.dev) | ✅ | ✅ | ✅ |
| **Auth / OAuth login** | ❌ (env/chave) | ✅ | ✅ | ✅ | ✅ |
| **Multimodal / imagens** | ❌ | ✅ | ✅ | ✅ | ✅ |
| **Menções `@arquivo` / `@agente`** | ❌ | ✅ | ✅ | ✅ | ✅ |
| **Arquitetura cliente/servidor** | ❌ | ✅ | 🟡 (SDK) | ✅ | ✅ (cloud) |
| **Integração IDE / VS Code** | ❌ | ✅ | ✅ | ✅ (é um IDE) | ✅ |
| **Integração GitHub / CI** | 🟡 (headless) | ✅ | ✅ (Actions) | 🟡 | ✅ |
| **Browser use / testes de navegador** | 🟡 (`browser_test`) | ❌ | 🟡 (MCP) | ✅ (nativo) | ❌ |
| **Temas / keybinds customizáveis** | ❌ | ✅ | 🟡 | ✅ | 🟡 |

---

## Sugestões de implementação para o Upcode

> **Já implementado** (ver a tabela e o Resumo): MCP (stdio), providers nativos
> + custo/tokens reais (Anthropic + Gemini), sessões persistentes
> (`/sessions`/`/resume`), undo de edições (`/undo`), modo Plan read-only
> (`/plan`) e regras de projeto (AGENTS.md + `/init`/`/rules`). Estes saíram
> desta lista. Abaixo ficam só as lacunas **pendentes**, ordenadas por impacto.

Cada item explica **a funcionalidade**, **como implementar no Upcode** e **o que
o Upcode já faz hoje** (quando há algo parcial).

### 1. Permissões granulares

- **O que é:** regras por ferramenta/comando — permitir, perguntar ou negar
  (ex.: `run_command` com `rm` sempre pergunta; `read_file` sempre permite).
- **Como implementar:** evoluir o `_confirm_hook` de `builtin_tools.py` para
  consultar uma política carregada de `settings.json` (listas allow/ask/deny com
  glob por tool e por padrão de comando). Suportar três modos globais (read-only,
  auto-workspace, full), espelhando Codex. O modo Plan (`/plan`) já é, na prática,
  o degrau "read-only" — reusar o mesmo `set_read_only`/guard como base.
- **Hoje no Upcode:** 🟡 — existe `set_confirm_hook` + toggle binário `/auto`
  (tudo ou nada) e confirmação em write/delete/run.

### 2. LSP / diagnósticos

- **O que é:** rodar language servers para dar ao agente erros/warnings reais
  após editar (em vez de editar "às cegas").
- **Como implementar:** `cowork/lsp.py` com um cliente LSP mínimo (stdio,
  `pylsp`/`tsserver`), iniciado por linguagem detectada no workspace. Após cada
  `edit_file`/`write_file`, enviar `didChange` e devolver os `diagnostics` como
  parte do resultado da tool, para o modelo corrigir na sequência.
- **Hoje no Upcode:** ❌ — sem diagnósticos; o agente só sabe de erros se rodar
  testes/linters via `run_command`.

### 3. Slash-commands customizados

- **O que é:** o usuário define seus próprios comandos (ex.: `/review`,
  `/commit`) em arquivos Markdown, com prompt parametrizável. (O `/init` citado
  no plano original já foi implementado junto das regras de projeto.)
- **Como implementar:** descobrir `*.md` em `<workspace>/.upcode/commands/`
  (mesmo padrão de `.agents`/`.skills`), cada um com frontmatter + corpo (template
  com `$ARGUMENTS`). Mesclar à lista estática `COMMANDS` do `tui.py` e executar
  injetando o corpo como prompt.
- **Hoje no Upcode:** ❌ — lista de comandos fixa em `COMMANDS` (`tui.py`).

### 4. Auth / OAuth e armazenamento de credenciais

- **O que é:** `upcode auth login` por provider (OAuth p/ Anthropic, Copilot, ou
  chave) com credenciais salvas com segurança, sem precisar de `.env`.
- **Como implementar:** subcomando `auth` no entrypoint (`upcode`), guardando
  tokens em `~/.upcode/auth.json` (permissão 600). `models.py` passa a resolver a
  chave nessa ordem: auth store → `api_key_env` → digitada na hora.
- **Hoje no Upcode:** ❌ — chave só por variável de ambiente/`models.json` ou
  digitada na hora (`/model`).

### 5. Multimodal / imagens e menções `@`

- **O que é:** anexar imagens (prints, diagramas) e referenciar arquivos/agentes
  com `@` direto no composer.
- **Como implementar:** no composer da TUI, detectar `@caminho` (autocompletar
  arquivos do workspace) e `@agente`, expandindo para o conteúdo do arquivo ou
  forçando a delegação. Para imagens, montar `content` multimodal (lista de
  partes `image_url`) — os providers nativos (Anthropic/Gemini) já existem;
  falta montar o `content` multimodal e a UI de anexar.
- **Hoje no Upcode:** 🟡 — **colar (paste)** aceita texto ou *objetos*. `Ctrl+V`
  (todas as plataformas) lê o clipboard do SO direto: imagem ou caminho(s) de
  arquivo viram um marcador atômico `[Image N]` / `[Document N]` / `[Video N]` /
  `[File N]` no composer (apagar o marcador remove o objeto); texto é inserido.
  O Cmd+V nativo do macOS cola texto (o terminal não encaminha Cmd, e clipboard
  só-imagem não gera evento de paste — por isso a captura de imagem é via
  Ctrl+V). Imagens viram bloco multimodal real, convertido por provider em
  `cowork/providers.py` (Anthropic `image`, Gemini `inline_data`, OpenAI
  `image_url`/`input_image`); arquivos não-imagem entram como caminho para o
  agente abrir com as tools. Clipboard em `cowork/clipboard.py` (macOS
  `pngpaste`/`osascript`, Linux `wl-paste`/`xclip`, Windows PowerShell). Falta
  ainda **menções `@arquivo`** (autocomplete/expansão); `@agente` já delega.

### 6. Formatters automáticos

- **O que é:** formatar o arquivo após editar (black/prettier/gofmt), mantendo o
  estilo do projeto.
- **Como implementar:** após `write_file`/`edit_file`, mapear extensão → comando
  de formatação (configurável em `settings.json`) e rodar via subprocess no
  arquivo tocado. Silencioso quando o formatter não está instalado.
- **Hoje no Upcode:** ❌.

### 7. Sandbox de execução

- **O que é:** isolar `run_command` (sem rede por padrão, restrito ao workspace)
  para reduzir risco e prompt injection.
- **Como implementar:** opção de executar comandos em sandbox por plataforma
  (`sandbox-exec` no macOS, `bwrap`/namespaces no Linux, ou container). Integrar
  com o modo de permissões (item 1): fora do workspace ⇒ pede aprovação.
- **Hoje no Upcode:** ❌ — `run_command` roda direto no shell com `cwd` no
  workspace, sem isolamento.

### 8. Ferramentas faltantes/imprecisas

- **O que é:** lacunas pontuais no conjunto de tools.
- **Como implementar:**
  - `glob` real (hoje o alias `glob`→`list_files` **não** faz glob): adicionar uma
    tool baseada em `pathlib.Path.rglob`/`fnmatch`.
  - `read_file` com `offset`/`limit` por linha (hoje só `max_chars`), para ler
    trechos grandes em pedaços.
  - `apply_patch` multi-arquivo (estilo Codex), para editar vários arquivos numa
    chamada.
  - Preview/aprovação de **diff por hunk** antes de gravar.
- **Hoje no Upcode:** 🟡 — `edit_file` faz substituição exata de string única;
  `read_file` trunca por `max_chars`; alias `glob` aponta para `list_files`.

---

## Bugs

### Imagens em providers sem visão (DeepSeek)

- **O que é:** ao colar uma imagem (`Ctrl+V` → marcador `[Image N]`) e enviar com
  um modelo **sem suporte a visão**, a requisição quebra. Os modelos da DeepSeek
  (`deepseek-chat`/`deepseek-reasoner`) são OpenAI-compatible mas **text-only**;
  o `content` multimodal (lista com `image_url`) que montamos em
  `cowork/providers.py` é rejeitado/ignorado pela API. Vale para qualquer modelo
  só-texto servido pela via OpenAI-compat, não só DeepSeek.
- **Como corrigir:** marcar em `models.json` quais perfis têm visão (flag
  `vision: true`) e, no envio, checar o modelo ativo: se **não** tem visão e há
  blocos de imagem, ou (a) **bloquear o paste** de imagem com um aviso na TUI, ou
  (b) **remover os blocos de imagem** antes de enviar, avisando que foram
  descartados (mantendo o texto/marcador). Idealmente também desabilitar o
  marcador `[Image N]` no composer quando o modelo atual não suporta visão.
- **Status:** ❌ aberto — não há checagem de capacidade; a imagem é enviada a
  qualquer modelo e o provider só-texto (ex.: DeepSeek) falha.

---

## Resumo

O Upcode já cobre bem o **núcleo de um agente** (loop de tools, subagentes em
Markdown, Skills, TUI, headless, compactação) e fechou boa parte das lacunas de
**integração e UX**: **MCP** (stdio), **provider Anthropic nativo** com
**custo/tokens reais**, **regras de projeto** (AGENTS.md + `/init`/`/rules`),
**sessões persistentes** (`/sessions`/`/resume`), **undo de edições** (`/undo`)
e **modo Plan read-only** (`/plan` toggle) — além de diferenciais frente a
alguns concorrentes (Agent Skills, delegação paralela explícita). As lacunas que
ainda diferenciam opencode, Claude Code, Antigravity e Codex estão em
**infraestrutura**: auth/OAuth, permissões granulares, LSP, slash-commands
customizados, multimodal/menções `@`, sandbox e formatters automáticos.

---

### Fontes

- [opencode — Docs](https://opencode.ai/docs/)
- [Build with Google Antigravity — Google Developers Blog](https://developers.googleblog.com/build-with-google-antigravity-our-new-agentic-development-platform/)
- [Features — Codex CLI | OpenAI Developers](https://developers.openai.com/codex/cli/features)
- [Agent approvals & security — Codex | OpenAI Developers](https://developers.openai.com/codex/agent-approvals-security)
</content>
</invoke>
