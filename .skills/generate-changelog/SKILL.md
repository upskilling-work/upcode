---
name: generate-changelog
description: >
  Generate a changelog or release notes from git history. Use when asked for a
  changelog, release notes, or a summary of what changed since a tag/version.
metadata:
  author: cowork-examples
  version: "1.0"
---

# Generate changelog

Produce Markdown release notes from the git history.

## Steps

1. Find the range. Use the latest tag as the start unless the user gives one:

   ```bash
   git describe --tags --abbrev=0        # latest tag (may fail if no tags)
   git log --pretty=format:'%h %s' <from>..HEAD
   ```

   If there are no tags, summarize the whole history (`git log --pretty=...`).
2. Group the commits by type, inferring from Conventional Commits prefixes when
   present (`feat`, `fix`, `perf`, `refactor`, `docs`, `chore`, `test`):
   - **Features** (`feat`)
   - **Fixes** (`fix`)
   - **Other** (everything else worth mentioning)
3. Rewrite each entry as a short, user-facing line (not the raw commit subject).
   Drop noise (merge commits, trivial chores) unless asked to keep everything.

## Output

A Markdown changelog. If the user asks to save it, write/prepend it to
`CHANGELOG.md` with `write_file`/`edit_file`; otherwise just return it:

```markdown
## <version or date>

### Features
- ...

### Fixes
- ...
```
