---
title: Installation
weight: 2
---

There are two ways to use Vectora: install a published desktop package or run the project from source. Desktop packages include the backend and frontend; Python and Node are not required to use the desktop app.

## Install a published release

Open the [Vectora GitHub releases](https://github.com/vectora-company/vectora/releases) and choose an artifact for your operating system and architecture. The current published build matrix covers:

| System | Published architecture | Available formats |
| --- | --- | --- |
| Windows | x64 and ARM64 | `.exe`, `.msi` |
| macOS | Apple Silicon (ARM64) | `.dmg` |
| Linux | x64 and ARM64 | `.AppImage`, `.deb`, `.rpm` |

CI builds these artifacts; check each release page to see which files were actually published. Package availability does not guarantee that every Linux distribution has the same system dependencies. On Linux, review the package dependencies and your distribution's requirements before installing.

On Windows, run the installer and follow its prompts. On macOS, open the DMG and drag the app to Applications. On Linux, use your package manager for `.deb`/`.rpm`; for AppImage, grant execute permission before opening it. Signing and notarization vary by artifact and should be checked in the release information.

## Run from source

Use this option for development or to inspect and modify the project. Also see the [contribution guide](https://github.com/vectora-company/vectora/blob/master/CONTRIBUTING.md).

**Requirements:** Python 3.13, [uv](https://docs.astral.sh/uv/), Node.js 26, and [pnpm](https://pnpm.io/).

```bash
git clone https://github.com/vectora-company/vectora.git
cd vectora

uv sync
pnpm --dir frontend install
```

Copy `.env.example` to `.env` if you want environment variables for development. Personal settings for desktop installs are stored in `~/.vectora/` (on Windows, `%USERPROFILE%\.vectora\`). See [Configuration](./configuration) and the [environment variable reference](../../reference/config).

Start the backend and serve the app:

```bash
uv run vectora start
```

To develop the frontend with Vite, run this in another terminal:

```bash
pnpm --dir frontend dev
```

The first account created on an instance gets initial administration access. On a server exposed to a network, configure access and signup according to [Authentication](../../security/authentication); do not expose the port directly to the internet without TLS and access controls.

## Providers and credentials

Configure at least one compatible chat provider. Embeddings, reranking, web search, transcription, and decision models are separate capabilities: configure only the provider needed for a feature you intend to use. Vectora does not require a Cohere key to start chat or a search key to use the workspace.

Credentials can be configured in **Settings → AI Models** or **Provider Routing**, depending on the provider type, or through environment variables. Read [Model settings](../../guides/using-settings) and [BYOK and privacy](../../security/byok-privacy) before sending data to an external service.

## License and services

The code is licensed under [AGPL-3.0](https://github.com/vectora-company/vectora/blob/master/LICENSE). Individual local use is free; features that depend on paid services may require an account or an active Pro entitlement. This does not change the code license terms.

## Next step

→ [Quick start](./quick-start)
