---
title: Agent Automation
weight: 6
---

Beyond answering in the current turn, Vectora's agent can delegate work to isolated subagents, schedule work for later, learn reusable patterns from a session, react to external webhooks, and receive messages from outside the chat UI.

## Delegate — isolated subagent execution

When the orchestrator delegates a coding task to the `coder` subagent, it can run that work in an isolated **git worktree** rather than directly in your main workspace checkout. This means a long-running or exploratory change doesn't collide with files you're actively editing yourself, and can be reviewed or discarded as its own branch before merging. Worktree creation reuses the same git logic the `git_worktree` tool already exposes, so it fails the same honest way (invalid branch, non-git workspace, disk full) rather than leaving a task stuck.

## Schedule — recurring tasks and one-off subagent runs

The agent can schedule work in two shapes:

- **Recurring tasks** (`schedule_task`) — described in plain-language time expressions ("every day at 9am", "every Friday at 6pm", "every 2 hours"), parsed deterministically into a cron schedule. An expression that doesn't match a known pattern is never guessed — it comes back as an error asking you to rephrase.
- **One-shot subagent runs** (`schedule_subagent_task`) — schedules a *specific* subagent (`coder` or `search`), not the full orchestrator, to run once at a future time ("in 30 minutes", "in 2 hours"). A scheduled `coder` run uses the same worktree isolation as Delegate above when a workspace is active.

Both surface in the Tasks tab of the workbench, distinguishing scheduled runs from work happening in the current turn.

## Remember — automatic learning, always with approval

Every 5 turns of a conversation, Vectora automatically reviews the transcript for reusable patterns — skills worth saving, facts worth remembering — and, if it finds anything, proposes them the next time you interact with that thread. Nothing is written automatically: the proposal sits pending until you approve or reject it, and a pending proposal blocks a new automatic trigger until it's resolved (so it doesn't queue up repeated proposals).

You can also trigger this manually, or have the agent save a specific fact or install a specific skill directly — both of those actions require your approval the same way, and both leave a visible artifact in the **Plan tab** once approved, so what Vectora learned about your project stays visible and consultable, not just a diff that scrolls away.

## Webhook-triggered automation

Beyond schedules, a background task can also be triggered by an inbound webhook — a GitHub PR opening, a GitHub issue changing state, or an alert from your observability stack. The event's payload is embedded in the agent's instruction, so it reads the same context a human would paste in. See [Webhook Templates](../webhook-templates) for the concrete models Vectora ships (PR review, issue sync, observability alerts) and [Observability Webhooks](../observability-webhooks) for the generic alerting contract.

## Vectora Connect — receiving messages from outside the chat UI

Vectora Connect delivers chat through platforms other than the built-in UI — Telegram (long polling), Discord (WebSocket Gateway), Slack (Socket Mode), and email (IMAP/SMTP) are implemented and running today, each translating that platform's native message format into the same turn the built-in chat UI produces, then replying back through that platform. Connect is a **Pro** feature — see [pricing](https://vectora.company/#pricing).

## See also

- [Webhook Templates](../webhook-templates) — the three webhook-triggered automation models
- [Sessions & Workspaces](../../concepts/sessions-and-workspaces) — what a workspace is and how trust works
- [Sandbox](../../concepts/sandbox) — sandboxing terminal/file access per workspace
- [Using the Workbench](../using-the-workbench) — the Tasks and Plan tabs in practice
