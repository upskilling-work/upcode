> [English version](README.md)

# Upcode

Interface Python para um **agente de código** construído sobre qualquer **API
compatível com OpenAI** — OpenAI, Azure OpenAI, Ollama, LM Studio, vLLM, Groq, etc.

Inclui:

- Um cliente sobre o SDK oficial `openai` (apontável para qualquer `base_url`).
- Histórico de conversa gerenciado automaticamente.
- **Function calling** com schema gerado a partir de anotações de tipo.
- Resposta completa (`send`) ou **streaming** (`stream`).
- **Agente de código + agentes opcionais**: o agente trabalha diretamente no
  loop de ferramentas e pode delegar para agentes focados em programação
  (`programmer`, `architect`, `designer`, `quality`, `pentest`, `devops`,
  `data`, `qatester`) via a ferramenta `delegate`.
- **Agent Skills**: capacidades reutilizáveis lidas de `.skills/` no workspace,
  carregadas sob demanda com `use_skill`.
- Interface de linha de comando interativa com `rich`.

## Instalação

### Instalação em uma linha (recomendado)

```bash
curl -fsSL https://raw.githubusercontent.com/upskilling-work/upcode/main/install.sh | bash
```

Isso irá:
1. Verificar `git` e Python 3.9+
2. Clonar o repositório em `~/.upcode`
3. Instalar todas as dependências
4. Criar um `.env` a partir de `.env.example`

Para instalar em um diretório personalizado:

```bash
UPCODE_INSTALL_DIR=~/meu-upcode curl -fsSL https://raw.githubusercontent.com/upskilling-work/upcode/main/install.sh | bash
```

### Instalação manual

```bash
git clone https://github.com/upskilling-work/upcode.git
cd upcode
pip install -r requirements.txt
cp .env.example .env   # edite com seu endpoint, chave e modelo
```

## Configuração

Configure via variáveis de ambiente (ou `.env`):

O Upcode usa apenas **duas** variáveis próprias (além das chaves de provedor que
`models.json` referencia via `api_key_env`):

| Variável | Descrição | Exemplo |
|---|---|---|
| `UPCODE_HOME_DIR` | Base dos subdiretórios `conf/`, `.agents/` e `.skills/` | _(padrão: sua própria localização)_ |
| `UPCODE_WORKSPACE` | Diretório de trabalho onde o agente opera | _(padrão: diretório atual)_ |
| `UPCODE_MAX_TOOL_ITERATIONS` | Máximo de rodadas de ferramentas por turno | _(padrão: `12`)_ |
| `OPENAI_API_KEY` (e similares) | Chaves de provedor, referenciadas por `models.json` | `sk-...` |

> O modelo em uso é escolhido com **`/model`** e salvo em `state.json`
> (na pasta de configuração) — a última seleção é restaurada automaticamente
> na próxima execução. Na ausência de uma seleção salva, usa o primeiro modelo
> utilizável de `models.json`. A pasta de configuração é `<UPCODE_HOME_DIR>/conf`
> (padrão: sua própria localização); é independente do `workspace` atual.

## Uso

```bash
python -m cowork.tui                   # TUI em tela cheia (estilo Codex) ⭐
```

### TUI (`python -m cowork.tui`)

Interface em tela cheia (Textual) no estilo Codex: área de conversa com scroll,
um *composer* com borda, streaming ao vivo do orquestrador e dos agentes,
indicador de "thinking…" e **Esc para interromper**.

Teclas: `Enter` envia · `Esc` interrompe · `Ctrl+L` limpa · `Ctrl+C` sai.
Comandos: `/workspace [dir]`, `/status`, `/agents`, `/reset`, `/help`, `/quit`.

`/workspace` sem argumento mostra o diretório atual; com um caminho
(`/workspace ~/projeto`) muda o diretório de trabalho — é onde as ferramentas
de arquivo passam a operar.

`/model` sem argumento lista os LLMs configurados; com um nome
(`/model qwen-coder`) troca o modelo em uso (orquestrador e agentes).

### Modelos (`conf/models.json`)

Os LLMs selecionáveis com `/model` ficam em `<UPCODE_HOME_DIR>/conf/models.json`:

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

Cada modelo tem `name` (usado em `/model`), `model` (id na API), `base_url` e
a chave — literal em `api_key` ou pelo nome de uma variável de ambiente em
`api_key_env`. Campos opcionais: `api` (`"chat"` padrão ou `"responses"` para
codex/GPT-5), `context_window`, `max_output` (vira o `max_tokens` da requisição)
e `temperature` (amostragem; use um valor baixo para código, ex.: `0.1` para `qwen-coder`).

`context_window` alimenta o **medidor de contexto**: a cada turno o Upcode
estima o uso e, quando passa 85% do orçamento (`context_window − max_output`),
avisa para rodar **`/compact`** — que pede ao próprio LLM um resumo dos turnos
antigos e os substitui, mantendo o prompt do sistema e o turno mais recente. Se
`context_window` for **0/ausente**, o contexto é tratado como **ilimitado** (sem
medidor ou aviso). Para modelos locais, use o mesmo valor de "Context Length"
carregado no LM Studio.

No submenu `/model` você pode **filtrar** digitando parte do nome ou provedor
(`claude`, `gpt`, `gemini`, `mini`…); cada item mostra a janela de contexto
(`ctx`) e o output máximo (`out`).

O `models.json` de exemplo já vem **organizado por empresa**, com as principais
APIs (todas via endpoints compatíveis com OpenAI) e os modelos locais:

| Empresa | Chave (`.env`) | Exemplos em `/model` |
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

Quando você seleciona um modelo cuja **chave não está disponível**, `/model`
pergunta como fornecê-la: **digitar a chave** na hora (mascarada) ou **usar a
variável de ambiente sugerida** (ex.: `XAI_API_KEY`). Modelos **locais** não
pedem chave.

> Claude e Gemini usam as **camadas compatíveis com OpenAI** desses provedores —
> a do Anthropic é um *shim* de compatibilidade, então recursos específicos do
> provedor (thinking adaptativo, etc.) podem não estar disponíveis por ela.

Digitar **`/`** abre um **menu de comandos** que filtra conforme você digita;
use `↑`/`↓` para navegar e `Enter` (ou clique) para selecionar. Selecionar
**`/model`** abre um **submenu com os modelos** de `models.json` para escolher.

A CLI inicia no **modo orquestrador**, com interação no estilo **Codex (OpenAI)**
— banner `>_`, prompt `›`, indicador "thinking…" e os comandos `/status`,
`/agents`, `/reset`, `/help`, `/quit`. O trabalho aparece em tempo real:

```
› o que o código faz?

• programmer  Listar e ler os arquivos do projeto.
  └ list_files(directory='.')
    .env  README.md  cowork/  examples/  requirements.txt
  └ read_file(path='README.md')
    # Upcode — uma interface Python para um agente de código…
› O projeto implementa um orquestrador que delega tarefas para agentes…
```

Cada `•` é um agente invocado; `└` é uma ferramenta que ele chamou (com o
resultado abaixo, em cinza) e o texto esmaecido é o que ele "pensa"/responde.
A linha começando com `›` é a síntese final do orquestrador.

Comandos de chat: `/quit`, `/reset`, `/agents`, `/help`.

## Uso — orquestrador (biblioteca)

```python
from cowork import Orchestrator, default_agents

orchestrator = Orchestrator(agents=default_agents())
print(orchestrator.send(
    "Liste os arquivos do projeto e escreva um resumo do que ele faz."
))
```

### Criar um agente (Markdown, estilo Claude Code)

A forma recomendada de adicionar um agente é criar um arquivo `.md` em
`.agents/` — um frontmatter YAML com `name`/`description` (e, opcionalmente,
`tools` e `model`) e o *system prompt* no corpo:

```markdown
---
name: suporte
description: Trata e diagnostica tickets de suporte técnico.
tools: read_file, search_code, run_command   # opcional; omitido = padrão
model: claude-sonnet                          # opcional
---
Você é um analista de suporte técnico. Seja objetivo e proponha a correção.
```

O app descobre agentes em `<UPCODE_HOME_DIR>/.agents` (biblioteca global) e em
`<workspace>/.agents` (os do projeto, que têm precedência). No campo `tools`
você pode usar os nomes do projeto (`read_file`, `write_file`, `edit_file`,
`search_code`, `run_command`, `fetch_url`, …), aliases no estilo Claude Code
(`Read`, `Write`, `Edit`, `Bash`, `Grep`, `WebFetch`) ou `all` para todos;
omitido, o agente recebe o conjunto padrão (arquivos + busca + internet + skills).
Copie `.agents/_template.md` para começar.

Criar um agente em código (API):

```python
from cowork import Orchestrator, Agent, AgentRegistry, ToolRegistry, tool

@tool
def buscar_ticket(id: int) -> str:
    """Busca um ticket de suporte pelo id."""
    return f"Ticket {id}: cliente reporta login lento."

reg = ToolRegistry(); reg.add(buscar_ticket)

suporte = Agent(
    name="suporte",
    description="Trata e diagnostica tickets de suporte.",
    system_prompt="Você é um analista de suporte técnico. Seja objetivo.",
    tools=reg,
)

equipe = default_agents()
equipe.add(suporte)

orchestrator = Orchestrator(agents=equipe)
print(orchestrator.send("Diagnostique o ticket 42 e escreva uma resposta ao cliente."))
```

Veja [examples/orchestrator.py](examples/orchestrator.py) para um exemplo completo.

## Uso — biblioteca

```python
from cowork import CoworkAgent, tool, ToolRegistry

@tool
def somar(a: int, b: int) -> int:
    """Soma dois números."""
    return a + b

reg = ToolRegistry()
reg.add(somar)

agent = CoworkAgent(tools=reg)
print(agent.send("Quanto é 21 + 21?"))   # o modelo chama a ferramenta `somar`

# streaming
for chunk in agent.stream("Explique o resultado em uma frase."):
    print(chunk, end="", flush=True)
```

Veja [examples/custom_tool.py](examples/custom_tool.py) para um exemplo completo.

## Estrutura

```
cowork/
  agent.py         # CoworkAgent + AgentConfig (loop de tool-calling)
  tools.py         # @tool, Tool, ToolRegistry (schema automático)
  builtin_tools.py # ferramentas (arquivos: read/write/edit/delete_file,
                   #   search_code (grep), run_command (shell),
                   #   calculate, current_time, fetch_url)
  manager.py       # Orchestrator (manager) + Agent (planeja e delega)
  agents.py        # carrega os agentes Markdown de .agents/
  skills.py        # Agent Skills: descoberta em .skills/ + use_skill
  models.py        # carrega os perfis de LLM de conf/models.json
  tui.py           # TUI em tela cheia (Textual, estilo Codex)
.agents/           # 1 arquivo .md por agente (estilo Claude Code)
  programmer.md  architect.md  designer.md  quality.md  pentest.md  devops.md  data.md  qatester.md
  _template.md     # template para criar um novo (arquivos com "_" são ignorados)
.skills/            # biblioteca global de Agent Skills (workspace: .upcode/skills/)
conf/
  models.json      # provedores/modelos selecionáveis via /model
  state.json       # última seleção de modelo (criado/atualizado em runtime)
examples/
  custom_tool.py
  orchestrator.py
```

### Agentes (pasta `.agents/`)

Cada agente é um arquivo `.md` em `.agents/` (estilo Claude Code), descoberto
em `<UPCODE_HOME_DIR>/.agents` e em `<UPCODE_WORKSPACE>/.agents` (o local do
projeto tem precedência). **Para criar um novo, basta adicionar um arquivo** —
copie `.agents/_template.md`:

```markdown
---
name: tradutor
description: Traduz textos entre idiomas.
# tools: opcional (omitido = padrão: arquivos + edição + grep + internet + skills)
# model: opcional (substitui o modelo do orquestrador)
---
Você é um tradutor profissional...
```

Arquivos cujo nome começa com `_` são ignorados. O carregador injeta as
ferramentas padrão (quando `tools` é omitido) e o lembrete de "agir em vez de
apenas descrever". Veja os detalhes do frontmatter na seção
[Criar um agente](#criar-um-agente-markdown-estilo-claude-code).

### Agent Skills

Skills são **capacidades reutilizáveis** descritas em arquivos, descobertas em
duas fontes (mescladas):

1. **`<UPCODE_HOME_DIR>/.skills/`** — biblioteca global/compartilhada;
2. **`<UPCODE_WORKSPACE>/.upcode/skills/`** — as skills locais do projeto.

Em nome repetido, a skill **local do workspace** tem precedência. Cada skill é
uma pasta com um `SKILL.md`:

```
.upcode/skills/
  conventional-commits/
    SKILL.md        # frontmatter (name, description) + instruções
    checklist.md    # (opcional) arquivos extras da skill
```

`SKILL.md` começa com um frontmatter simples e o corpo com as instruções:

```markdown
---
name: conventional-commits
description: Escreve mensagens de commit no padrão Conventional Commits.
---
# instruções
1. ...
```

Funciona por **divulgação progressiva**: na inicialização, o Upcode lista as
skills disponíveis (nome + descrição) nos prompts dos agentes e do orquestrador;
quando a tarefa corresponde a uma skill, o agente carrega as instruções sob
demanda com a ferramenta **`use_skill(<name>)`** (e `list_skills` lista todas).
Os arquivos extras da skill são lidos com `read_file` nos caminhos indicados. As
skills seguem o **workspace em uso** — trocar de projeto com `/workspace` muda o
conjunto de skills.

### Regras do projeto (`AGENTS.md`)

Um **arquivo de regras** carrega instruções específicas do projeto — convenções,
comandos de build/teste, restrições — que devem guiar cada turno. Ao contrário
das skills (carregadas sob demanda), as regras são **lidas automaticamente** e
injetadas no prompt do orquestrador e de cada agente, então sempre se aplicam.
Nenhuma chamada de ferramenta necessária.

Nomes de arquivo reconhecidos, em ordem de preferência por diretório:
**`AGENTS.md`**, `UPCODE.md`, `CLAUDE.md`. Descoberta, com a mais local tendo
precedência:

1. **`<UPCODE_HOME_DIR>/AGENTS.md`** — arquivo de regras global/compartilhado;
2. da **raiz git até o workspace**, o primeiro arquivo reconhecido em cada
   diretório (assim as regras da raiz de um monorepo se aplicam antes das de um
   pacote). Fora de um repositório git, apenas o diretório workspace é inspecionado.

```markdown
# meu-projeto
## Build & test
- Python: `pip install -e .` para configurar, `pytest` para rodar os testes.
## Convenções
- Sempre use type hints; nunca faça commit de segredos.
```

Comandos: **`/rules`** lista os arquivos de regras em vigor; **`/init`** inspeciona
o repositório (marcadores de stack, layout) e escreve um esqueleto `AGENTS.md`
pronto para editar, carregando-o no prompt imediatamente. Trocar de projeto com
`/workspace` recarrega as regras (e os agentes/skills) para o novo projeto.

### Servidores MCP (`conf/mcp.json`)

O Upcode pode usar ferramentas fornecidas por servidores **MCP** (Model Context
Protocol) externos — filesystem, git, bancos de dados, APIs internas — sem
hard-codá-las. Defina os servidores em **`<UPCODE_HOME_DIR>/conf/mcp.json`**
(global) e/ou **`<workspace>/.upcode/mcp.json`** (local do projeto, que vence em
conflito de nome), usando o formato `mcpServers`:

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

Cada servidor habilitado é iniciado na inicialização (transporte stdio — um
comando local falando JSON-RPC via stdin/stdout) e suas ferramentas ficam
disponíveis como **`mcp_<servidor>_<ferramenta>`**. Um servidor que falha ao
iniciar é reportado e ignorado, nunca bloqueando o app. **`/mcp`** lista os
servidores conectados e suas ferramentas. Copie
[`conf/mcp.json.example`](conf/mcp.json.example) para começar. O cliente MCP
usa apenas a biblioteca padrão — sem dependência extra.

### Provedores nativos e custo

Além de qualquer endpoint compatível com OpenAI, o Upcode se comunica com a
**Anthropic nativamente** (sua própria Messages API, via `httpx` — sem SDK
extra). O modo nativo desbloqueia recursos específicos do provedor que o shim de
compatibilidade descarta, notavelmente o **extended thinking**. Escolha por modelo
em `models.json` com `"api": "anthropic"` (os perfis `claude-*` incluídos já
usam isso); o perfil `claude-sonnet-thinking` habilita thinking via
`reasoning_effort: high` (ou defina `thinking_budget` em tokens).

`models.json` também aceita **`input_cost`/`output_cost`** (USD por 1M de tokens,
convenção models.dev). O Upcode rastreia o **uso real de tokens** retornado pela
API (entre o orquestrador e todos os sub-agentes), alimenta o medidor de contexto
e mostra o **custo em `$`** em **`/status`**.
