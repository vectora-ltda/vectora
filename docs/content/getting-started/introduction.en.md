---
title: Introduction
weight: 1
---

## What Vectora is

Vectora is a **self-hosted AI workspace** for development teams. It runs entirely on your server — your VPS, your local machine, your company's server — and solves two problems most AI assistants ignore: giving you and the agent the **same working surfaces** (filesystem, terminal, git, browser — not a chat narrating actions you never see), and **making the agent truly know your project**, not just generate generic code.

This happens through two complementary paths:

- **Hybrid RAG** — indexing code, docs, and past decisions with keyword search (BM25) + dense vector search + reranking, so the agent answers based on what was indexed, not on what it "thinks" is true.
- **Native Context Graph** — a workspace knowledge graph (functions, classes, concepts, and how they relate), built via AST parsing (tree-sitter) and LLM semantic extraction. Complements RAG with structural context embeddings alone can't capture.

## Architecture in one picture

```text
You (CLI / Web Chat / MCP client)
        │
        ▼
   Orchestrator (native engine — imperative loop)
        │
   ┌────┴────┐
   ▼         ▼
 coder     search      ← specialized subagents
   │         │
   └────┬────┘
        ▼
  Native tools (files, git, terminal, browser, RAG, web, integrations)
        │
        ▼
  SQLite + LanceDB (lite, default)  or  Postgres + Qdrant + Redis (complete)
```

The **orchestrator** is the supervisor: it answers directly for simple questions, or delegates to a specialized subagent (`coder` for file/git/terminal operations, `search` for web search and RAG) through human-in-the-loop (HITL) middleware that pauses before destructive actions for your approval.

## Three ways to use it

The same backend serves three different surfaces, at the same time:

1. **Web chat** — multi-user React interface, with a workbench (files, git, terminal, browser, RAG, Context Graph, Kanban) shared between you and the agent — see [Using the chat](../guides/using-the-chat) and [Using the workbench](../guides/using-the-workbench).
2. **CLI** — `vectora start`, `vectora config`, `vectora storage` — see the [CLI reference](../reference/cli).
3. **MCP client** — Vectora connects to MCP servers you install (a connector marketplace, plus manual registration) so the agent can use their tools. Vectora does not expose itself as an MCP server to other harnesses — see [MCP client](../reference/mcp-client).

## Open source and operation

Vectora is distributed under the [AGPL-3.0 license](https://github.com/vectora-ltda/vectora/blob/master/LICENSE). You can run it on your machine or infrastructure and study, modify, and redistribute the code under the license terms. The license does not mean every feature has been validated for production or that external services are free.

Local storage uses SQLite and LanceDB. AI providers and external integrations are configured by the operator; costs, retention, and use of data sent to them follow each connected service's policies. Features that depend on Vectora Services may require an account or entitlement. See the [repository](https://github.com/vectora-ltda/vectora), the [contribution guide](https://github.com/vectora-ltda/vectora/blob/master/CONTRIBUTING.md), and [releases](https://github.com/vectora-ltda/vectora/releases) for verifiable information.

## Next step

→ [Installation](../installation)
