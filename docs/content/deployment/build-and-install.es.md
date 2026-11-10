---
title: Building from Source
weight: 3
---

El pipeline de build oficial usa [SCons](https://scons.org/) como orquestador, ejecutado desde la raíz del repositório.

## Pipeline

```text
build frontend (Vite)  →  build híbrido del backend  →  Electron + electron-builder
frontend/dist/              dist/vectora/vectora(.exe)   frontend/dist-electron/
```

El "build híbrido" compila **solo el paquete del backend** a C vía Nuitka (`--mode=package`, produciendo un `.pyd`), y luego usa **PyInstaller** para empaquetar el launcher + ese módulo compilado + las libs de Python en un único ejecutable. Compilar solo el backend (en lugar de Nuitka onefile puro) evita quedarse sin memoria al compilar dependencias gigantes (`google.genai.types`, LanceDB) directo a C.

## Comandos

```powershell
scons release          # build completo + instalador nativo para el SO actual
```

## Requisitos de build

- Python 3.13 (fijado — la versión de Nuitka en uso aún no soporta 3.14)
- [uv](https://docs.astral.sh/uv/)
- Node.js 26 y `pnpm`
- En Windows: Visual Studio Build Tools (MSVC) + Windows SDK, para el toolchain C de Nuitka

## Calidad

```powershell
scons tests       # suite completa (pytest + vitest)
scons coverage    # misma suite, con reporte de cobertura
scons lint        # ruff + ty + bandit (Python) + tsc + oxlint (TypeScript)
```

## Infraestructura Docker (no la app)

```powershell
scons docker      # levanta Postgres + Redis + Qdrant para el modo completo
```

Ver [Docker](../docker) y [Storage](../../concepts/storage).
