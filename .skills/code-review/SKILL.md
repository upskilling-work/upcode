---
name: code-review
description: >
  Review the current code changes for bugs, risks and quality issues and report
  them by severity. Use when asked to review changes, a diff or a PR, or to
  check code before committing.
metadata:
  author: cowork-examples
  version: "1.0"
---

# Code review

Review the working-tree changes and report concrete, actionable findings.

## Steps

1. Get the changes with `run_command`:

   ```bash
   git diff
   git diff --staged
   ```

   If both are empty, ask the user what to review (a file, a path or a range)
   and read it with `read_file`.
2. Read enough surrounding context (`read_file`/`search_code`) to judge the
   change — do not review a diff in isolation.
3. Look for, in priority order:
   - **Correctness**: logic bugs, off-by-one, wrong conditions, unhandled
     `None`/null, race conditions, broken error handling.
   - **Security**: injection, secrets in code, unsafe input, auth gaps.
   - **Edge cases**: empty/large inputs, failures, concurrency.
   - **Quality**: duplication, dead code, naming, missing tests, style that
     diverges from the surrounding code.

## Output

Group findings by severity and be specific:

```
HIGH
- path/to/file.py:42 — <problem>. Fix: <concrete suggestion>.
MEDIUM
- ...
LOW / NITS
- ...
```

State explicitly if you found nothing serious. Do NOT rewrite the code unless
the user asks — this skill reports; it does not edit.
