---
title: Documentation
type: docs
cascade:
  type: docs
sidebar:
  open: true
---

Vectora is an **AI-assisted development workspace** that runs on infrastructure you control. You and the agent work side by side in the same filesystem, terminal, Git, and browser; users configure MCP connectors. Models can be local or remote: when you choose an external provider, the required requests are sent to that service.

At its core, Vectora closes the **knowledge gap** between an LLM and your current codebase, docs, and stack: a hybrid **RAG** pipeline (BM25 + dense vectors + reranker) for similarity retrieval, and a native **Context Graph** (workspace analyzed via tree-sitter + LLM extraction) for structural context.

## Where to start

| I want to...                             | Go to                                          |
| ----------------------------------------- | ----------------------------------------------- |
| Install Vectora                           | [Installation](./getting-started/installation)   |
| Run it in 5 minutes                       | [Quick start](./getting-started/quick-start) |
| Understand the RAG pipeline               | [RAG & Context Graph](./concepts/rag)          |
| Connect an MCP server (as a client)       | [MCP client](./reference/mcp-client)         |
| See all CLI commands                      | [CLI reference](./reference/cli)             |
| Deploy on a server                        | [Requirements](./deployment/requirements)      |
| Understand auth, secrets, and BYOK        | [Security](./security/authentication)          |
| Explore the 0.3 release line               | [What's new in 0.3](./guides/release-0.3)       |

## What Vectora is (and isn't)

Vectora is **commercial, closed-source software** — it isn't open source. You run it on your own infrastructure (your server, your VPS, your desktop), but the source code belongs to Vectora Company. It's the same model as Cursor, Linear, or Notion: the infra is yours, the code is the vendor's.

- **Free** runs the local instance without requiring an account. External providers and connectors send the required requests to configured services; local models can be used when available.
- **Pro** is optional and covers trial/billing/licensing via `services.vectora.company`, a small Cloudflare Worker — it isn't a "Vectora Cloud" that hosts or runs your instance for you. Upgrading changes which features are available (high-performance storage stack, multi-user web chat, webhook-triggered automations), never where the agent runs.

See the [pricing page](https://vectora.company/#pricing) for current plans.

## Limits of the 0.3 line

- **Local execution:** the sandbox turns on automatically only when the workspace has no `vectora.toml` and the environment supports native isolation; with a `vectora.toml`, the `[sandbox]` section applies (`enabled = false` in the template). The `local` backend runs on Linux (on Windows, through WSL2). `allow_tcp_ports` requires Landlock ABI V4 (kernel 6.5+), and an empty list does not restrict the network. See [Sandbox](./concepts/sandbox) and [Egress policies](./guides/egress-policies).
- **Remote providers:** with BYOK keys or Ollama, OpenRouter, and 9router endpoints, requests go from the machine running the backend straight to the chosen provider.
- **Skipped validations:** live tests marked as `skipped` do not prove live behavior; cassette replay does not replace a test against the real provider. See [Verification and Evaluation Lab](./guides/verification-lab).
