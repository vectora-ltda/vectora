---
title: What's new in version 0.1.23
weight: 1
---

Version 0.1.23 brings together Vectora's evolution from a tool-enabled workspace into an agent runtime with identifiable runs, evidence, planning, policies, and evaluation. This page describes the capabilities included in that version.

## What's included

The sections below group the line's capabilities by area, with the limits that apply to each one.

### Persistent runs and history

AgentRun associates lifecycle state, a `run_id`, evidence, and parent/subagent relationships. The event ledger supports thread history and reconstruction. Local storage uses SQLite; distributed deployments can configure PostgreSQL. Checkpoints and resume behavior depend on the execution path and configured storage.

See [sessions and workspaces](../../concepts/sessions-and-workspaces) and the [agent reference](../../reference/agents).

### Plans, subagents, and Kanban

Plan Mode can break a request into executable steps and dependencies. Kanban organizes background tasks and tracks their executions; policies and gates still determine what requires approval. Parallel delegation is best suited to independent tasks, with each profile limited to its allowed tools.

See [subagents](../../concepts/sub-agents), [agent automation](../agent-automation), and [using chat](../using-the-chat). The [Plan Mode, AgentRun, and decisions](../plan-mode-and-agentrun) guide covers Plan Mode, AgentRun states, and recorded decisions.

### Configurable HITL and Decision Models

To configure it:

1. Open **Settings → AI Models** and select a decision provider.
2. Configure credentials and check the runtime readiness status.
3. Enable only the surfaces where the model may be consulted.
4. In chat, choose whether to use the configured model for that run.
5. Remember that model decisions do not replace policy, permissions, or approval gates.

An evaluation that starts and then fails must not release the action. A missing or unavailable model is not an approved decision; static controls remain active. See [Decision Models](../decision-models) and [settings](../using-settings).

### Verification and Evaluation Lab

Quality gates evaluate outcomes after execution and distinguish passed, failed, blocked, degraded, and unverified results. The CLI does not directly capture live interactions. Enable `EVALUATION_RECORDING_ENABLED=true` before a run to record its model interactions in AgentRun. Save the authenticated `GET /runs/{run_id}` JSON response, then use `vectora eval export-run --run-file run.json --target cassette.json`; it calls `cassette_from_run` and rejects runs without recorded interactions. Deleting the session removes its stored trace, but exported files require separate retention and deletion. Only known credential fields and patterns are hidden; messages and responses can retain private code or personal data. Restrict access and review cassettes before sharing.

Use `vectora eval run` to replay a single cassette and `vectora eval compare` to compare results. Replay validates reproducible cassette behavior, not live model, Browser Workbench, or external service availability. See [Verification and Evaluation Lab](../verification-lab) to configure quality-gate profiles.

### Memory, context, and RAG

Context Planner and Context Graph organize project context; persistent memory and buckets separate knowledge sets. Ingestion indexes documents for vector retrieval and, depending on storage mode and configured providers, hybrid retrieval and reranking.

Add documents to the workspace, wait for the embedding queue to process them, and inspect search results before relying on the index. See [RAG](../../concepts/rag), [Context Graph](../../concepts/context-graph), and [storage](../../concepts/storage).

### Browser Workbench and policy/egress

Browser Workbench navigation uses Electron Chromium when the sidecar and UI-prepared workspace session are available. In the default Tavily path, `fetch_url` uses Chromium as a fallback without an API key or after quota/rate-limit errors. `web_search` sends queries to the configured search provider (for example Tavily) and may fall back to the DuckDuckGo API. `web_crawl` and `web_map` require `TAVILY_API_KEY` and have no Chromium fallback. Fetch, crawl and map check their initial URL against egress; Search does not apply that destination policy. The Chromium fallback does not revalidate redirects or pin the destination IP.

Policy snapshots and egress checks apply to connected tool paths. Coverage is specific to each adapter; an integration that is not connected does not inherit controls merely because this page describes them. See [Egress policies](../egress-policies), [workbench](../using-the-workbench), and [security](../../security).

## Validate your environment

- Check the installed version and each provider's readiness before starting a run.
- Start with a simple chat run, then exercise approval, denial, and error paths with a controlled action.
- For local providers, verify that the service and required model weights are available.
- Keep `passed`, `failed`, and `skipped` results distinct. A `skipped` test does not validate the live path.
- Do not use replay instead of a live call when live integration is what you need to validate.
