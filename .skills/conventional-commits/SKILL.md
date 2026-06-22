---
name: conventional-commits
description: Escreve mensagens de commit no padrão Conventional Commits (feat, fix, etc.).
---

# Conventional Commits

Ao escrever uma mensagem de commit, siga o formato:

```
<tipo>(<escopo opcional>): <resumo no imperativo, minúsculo, sem ponto final>

<corpo opcional explicando o porquê>
```

Tipos permitidos: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`,
`build`, `ci`.

Regras:
1. O resumo tem no máximo 72 caracteres e usa o imperativo ("adiciona", não
   "adicionado").
2. Use `feat` para novas funcionalidades e `fix` para correções de bug.
3. Mudanças que quebram compatibilidade levam `!` após o tipo/escopo e uma
   seção `BREAKING CHANGE:` no corpo.

Exemplo:

```
feat(skills): adiciona descoberta de skills em .skills/

Carrega cada SKILL.md (frontmatter + instruções) e expõe a ferramenta use_skill.
```
