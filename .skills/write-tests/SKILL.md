---
name: write-tests
description: >
  Write and run unit tests for a given file or module. Use when asked to add
  tests, write tests, or improve test coverage for some code.
metadata:
  author: cowork-examples
  version: "1.0"
---

# Write tests

Add focused unit tests for the requested code and verify they pass.

## Steps

1. Read the target file(s) with `read_file` to understand the public behavior
   and edge cases.
2. Detect the project's test setup by inspecting the repo (`list_files`,
   `search_code`): test framework and conventions (e.g. `pytest` + `tests/` for
   Python, `jest`/`vitest` for JS), and how tests are run (`package.json`
   scripts, `pyproject.toml`, `Makefile`).
3. Write tests with `write_file` in the conventional location and naming. Cover:
   - the happy path,
   - important edge cases (empty/boundary/invalid input),
   - error handling (exceptions / failure modes).
   Keep tests small, isolated and deterministic; avoid network/filesystem unless
   already mocked in the project.
4. Run the tests with `run_command` (e.g. `pytest -q <file>`), and iterate until
   they pass.

## Output

Report which test file(s) you created, what they cover, and the real result of
running them (pass/fail counts). Never claim tests pass without running them.
