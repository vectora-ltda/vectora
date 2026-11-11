---
title: Updates
weight: 5
---

## Desktop app

Vectora Desktop checks for updates using the channel configured for the installation. When a version is available, the app shows its status and release notes, creates a rotating backup of local data, and downloads the package without installing it immediately. Installation is applied when the app exits. Users can control automatic checks in preferences and start a manual check.

Behavior can depend on operating system, installed package format, and published channel. See [GitHub releases](https://github.com/vectora-company/vectora/releases) for available versions and artifacts.

## Source builds

An instance started with `uv run vectora start` does not use the Electron updater. Update the checkout and dependencies using the development flow in [Installation](../../getting-started/installation).
