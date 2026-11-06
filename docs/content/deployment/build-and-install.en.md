---
title: Building from Source
weight: 3
---

The official build pipeline uses [SCons](https://scons.org/) as the orchestrator, run from the repository root.

## Pipeline

```text
build frontend (Vite)  →  hybrid backend build  →  Electron + electron-builder
frontend/dist/              dist/vectora/vectora(.exe)   frontend/dist-electron/
```

The "hybrid build" compiles **only the backend package** to C via Nuitka (`--mode=package`, producing a `.pyd`), then uses **PyInstaller** to package the launcher + that compiled module + the Python libs into a single executable. Compiling only the backend (instead of pure Nuitka onefile) avoids running out of memory compiling giant dependencies (`google.genai.types`, LanceDB) straight to C.

## Commands

```powershell
scons release          # full build + native installer for the current OS
```

## Build prerequisites

- Python 3.13 (pinned — the Nuitka version in use doesn't support 3.14 yet)
- [uv](https://docs.astral.sh/uv/)
- Node.js 26 and `pnpm`
- On Windows: Visual Studio Build Tools (MSVC) + Windows SDK, for Nuitka's C toolchain

## Quality

```powershell
scons tests       # full suite (pytest + vitest)
scons coverage    # same suite, with coverage report
scons lint        # ruff + ty + bandit (Python) + tsc + oxlint (TypeScript)
```

## Infrastructure Docker (not the app)

```powershell
scons docker      # spins up Postgres + Redis + Qdrant for complete mode
```

See [Docker](../docker) and [Storage](../../concepts/storage).
