---
name: dockerize
description: >
  Create a Dockerfile and .dockerignore to containerize the project. Use when
  asked to dockerize, containerize, or add Docker support to the app.
compatibility: The generated image targets the project's language/runtime.
metadata:
  author: cowork-examples
  version: "1.0"
---

# Dockerize

Add a sensible Dockerfile (and .dockerignore) for the current project.

## Steps

1. Detect the stack by inspecting the repo (`list_files`, `read_file`):
   - Python: `requirements.txt` / `pyproject.toml`
   - Node: `package.json` (and lockfile)
   - Go: `go.mod`; etc.
   Identify the entry point (how the app starts) and the runtime version.
2. Write a **`Dockerfile`** with `write_file`, following good practice:
   - pin a slim base image for the detected runtime,
   - install dependencies in a separate, cacheable layer (copy manifests first),
   - prefer a multi-stage build for compiled languages,
   - run as a non-root user,
   - set the correct `CMD`/`ENTRYPOINT` and `EXPOSE` the right port.
3. Write a **`.dockerignore`** excluding VCS, deps and build artifacts
   (`.git`, `node_modules`, `__pycache__`, `.venv`, `dist`, `build`, etc.).
4. If Docker is available, validate the build:

   ```bash
   docker build -t <project-name> .
   ```

## Output

State which files you created and the exact commands to build and run the
image (`docker build …` / `docker run …`). If `docker build` fails, report the
real error and fix the Dockerfile.
