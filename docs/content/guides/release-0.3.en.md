---
title: What's new in the 0.3 line
weight: 1
---

The `release/0.3` line expands Vectora from a tool-enabled workspace into an agent runtime with identifiable runs, evidence, planning, policies, and evaluation. This page describes the line's composition, including PR #404 stacked on top of it. The `0.3.0` tag must still be published before this content represents a distributed release.

## What's included

### Persistent runs and history

AgentRun associates lifecycle state, a `run_id`, evidence, and parent/subagent relationships. The event ledger supports thread history and reconstruction. Local storage uses SQLite; distributed deployments can configure PostgreSQL. Checkpoints and resume behavior depend on the execution path and configured storage.

See [sessions and workspaces](../../concepts/sessions-and-workspaces) and the [agent reference](../../reference/agents).

### Plans, subagents, and Kanban

Plan Mode can break a request into executable steps and dependencies. Kanban organizes background tasks and tracks their executions; policies and gates still determine what requires approval. Parallel delegation is best suited to independent tasks, with each profile limited to its allowed tools.

See [subagents](../../concepts/sub-agents), [agent automation](../agent-automation), and [using chat](../using-the-chat). The [Plan Mode, AgentRun, and decisions](../plan-mode-and-agentrun) guide covers Plan Mode, AgentRun states, and recorded decisions.

### Configurable HITL and Decision Models

In PR #404, **Settings → AI Models** brings AI providers and the surfaces allowed to consult Decision Models together. The chat exposes a Decision Model menu and an HITL panel. Clef, Laya, JEV, or another compatible provider can return a structured `allow`, `deny`, or `requires_human` decision.

To configure it:

1. Open **Settings → AI Models** and select a decision provider.
2. Configure credentials and check the runtime readiness status.
3. Enable only the surfaces where the model may be consulted.
4. In chat, choose whether to use the configured model for that run.
5. Remember that model decisions do not replace policy, permissions, or approval gates.

An evaluation that starts and then fails must not release the action. A missing or unavailable model is not an approved decision; static controls remain active. See [Decision Models](../decision-models) and [settings](../using-settings).

### Verification and Evaluation Lab

Quality gates evaluate outcomes after execution and distinguish passed, failed, blocked, degraded, and unverified results. Cassettes record interactions with sensitive data redacted; replay lets you compare a recorded run without calling the external provider again.

Use `vectora eval run` to run a cassette and `vectora eval compare` to compare results. Replay validates reproducible cassette behavior, not live model, Browser Workbench, or external service availability. See [Verification and Evaluation Lab](../verification-lab) to configure quality-gate profiles.

### Memory, context, and RAG

Context Planner and Context Graph organize project context; persistent memory and buckets separate knowledge sets. Ingestion indexes documents for vector retrieval and, depending on storage mode and configured providers, hybrid retrieval and reranking.

Add documents to the workspace, wait for the embedding queue to process them, and inspect search results before relying on the index. See [RAG](../../concepts/rag), [Context Graph](../../concepts/context-graph), and [storage](../../concepts/storage).

### Browser Workbench and policy/egress

When the Electron sidecar and UI-prepared session are active, the runtime uses integrated Chromium for search and browsing. Search, fetch, crawl, and map share applicable URL and destination controls; page, depth, and origin limits constrain collection. The HTTP fallback is not the same browser session and does not inherit its cookies.

Policy snapshots and egress checks apply to connected tool paths. Coverage is specific to each adapter; an integration that is not connected does not inherit controls merely because this page describes them. See [Egress policies](../egress-policies), [workbench](../using-the-workbench), and [security](../../security).

## Validate your environment

- Check the installed version and each provider's readiness before starting a run.
- Start with a simple chat run, then exercise approval, denial, and error paths with a controlled action.
- For local providers, verify that the service and required model weights are available.
- Keep `passed`, `failed`, and `skipped` results distinct. A `skipped` test does not validate the live path.
- Do not use replay instead of a live call when live integration is what you need to validate.

PRs [#382](https://github.com/vectora-ltda/vectora/pull/382), [#400](https://github.com/vectora-ltda/vectora/pull/400), [#404](https://github.com/vectora-ltda/vectora/pull/404), [#408](https://github.com/vectora-ltda/vectora/pull/408), and [#414](https://github.com/vectora-ltda/vectora/pull/414) document the line's composition and follow-ups. Published tags and artifacts are the final source of availability information.
