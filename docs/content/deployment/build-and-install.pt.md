---
title: Build a partir do Código-Fonte
weight: 3
---

O pipeline de build oficial usa [SCons](https://scons.org/) como orquestrador, rodado da raiz do repositório.

## Pipeline

```text
build frontend (Vite)  →  build híbrido do backend  →  Electron + electron-builder
frontend/dist/               dist/vectora/vectora(.exe)    frontend/dist-electron/
```

O "build híbrido" compila **só o pacote do backend** em C via Nuitka (`--mode=package`, gerando um `.pyd`), e usa **PyInstaller** pra empacotar o launcher + esse módulo compilado + as libs Python num executável único. Compilar só o backend (em vez de Nuitka onefile puro) evita esgotamento de memória ao compilar dependências gigantes (`google.genai.types`, LanceDB) direto pra C.

## Comandos

```powershell
scons release          # build completo + instalador nativo pro SO atual
```

Nuitka compila nativo ao host — não faz cross-compile. `scons release` sempre
gera o instalador do SO em que você está rodando; os instaladores dos outros
SOs saem da matriz `release-native` no GitHub Actions (um runner por SO).

## Pré-requisitos de build

- Python 3.13 (fixado — o Nuitka usado ainda não suporta 3.14)
- [uv](https://docs.astral.sh/uv/)
- Node.js 26 e `pnpm`
- No Windows: Visual Studio Build Tools (MSVC) + Windows SDK, pro toolchain C do Nuitka

## Qualidade

```powershell
scons tests       # suíte completa (pytest + vitest)
scons coverage    # mesma suíte, com relatório de cobertura
scons lint        # ruff + ty + bandit (Python) + tsc + oxlint (TypeScript)
```

## Docker de infraestrutura (não o app)

```powershell
scons docker      # sobe Postgres + Redis + Qdrant pro modo complete
```

Veja [Docker](../docker) e [Storage](../../concepts/storage).
