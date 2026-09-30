---
title: Using Settings
weight: 3
---

Everything you configure through the UI lives in three separate dialogs, each with a clear purpose.

## Preferences

Personal settings, for your user.

- **General** — theme (system/light/dark/preset/custom), language, custom system prompt, model fallback order, and custom colors (background, foreground, card, border, primary, accent, muted, sidebar, user bubble color).

The **Godot Dark** and **Godot Light** presets apply a Godot palette to the whole shell and keep Monaco in sync. They cover GDScript, `.tscn` scenes, `.tres` resources, `project.godot`, `.gdshader`/`.gdshaderinc` shaders, and the `gdscript`, `gd`, `tscn`, `tres`, `godot`, and `gdshader` fences in Markdown and chat. Other presets keep their shell appearance and only use the Godot language rules in code.

This delivery is lexical: comments, strings, types, keywords, properties, node references, folding, and brackets. It does not provide semantic autocomplete, engine diagnostics, or F1 documentation. The palette approximates Godot's default theme and pre-blends editor states for Monaco; breakpoint and bookmark colors are part of the color contract only.
- **Memory** — list of persistent memories (key-value pairs). Add, edit inline, delete one or clear all, with a last-updated timeline.
- **Account** — name (editable), email (read-only), role (root/admin/member/viewer).

## Environment

Integration and extensibility settings, in two tabs.

- **Integrations** — external connectors (manual API key or OAuth — GitHub, GitLab, Google, Slack, etc.) side by side with your custom per-user environment variables (masked in the UI, never exposed in plain text). Shows connected/disconnected status and the webhook URL when applicable.
- **Provider Routing** — local and dynamic LLM providers, e.g. discovering and selecting models served by a local Ollama instance.

Installing MCP connectors and skills from a curated catalog is handled by the workbench's **Library** tab, not here — see [Using the Workbench](../using-the-workbench).

## Administration (root/admin)

Instance-wide settings — only visible to administrators.

- **Users** — list with role, created at, last login. Change role, delete a user. **Invites** section: create an invite (role + optional email + 1–720h TTL), copy URL, revoke pending invites.
- **Tools** — list of global tools by category, with an enable/disable toggle per tool.
- **Safe Folders** — a whitelist of paths that require extra approval even after being trusted.
- **System** — backend version, Python version, platform, status of each service, recent observability span count.
- **Configuration** — `allow_public_signup`, the instance's default model, agent max recursion depth, database DSN (read-only), integration token.

## See also

- [Security: Authentication and RBAC](../../security/authentication)
- [MCP client](../../reference/mcp-client) — how the Environment tab's MCP plugins connect
