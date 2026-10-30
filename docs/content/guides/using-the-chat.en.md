---
title: Using the Chat
weight: 1
---

## Sending messages

The chat input accepts text, drag-and-dropped files, pasted clipboard images, and file `@mentions` to bring specific content into context without having to describe it.

## Model selector

Only shows providers with a configured API key — Google Gemini, OpenAI, Anthropic, Cohere, or local Ollama. Switching models mid-conversation doesn't lose history.

## Permission modes

Control how automatically the agent acts before asking for your approval:

| Mode             | Approval-gate behavior                                                                       |
| ---------------- | -------------------------------------------------------------------------------------------- |
| **Always ask**   | pauses for tools classified as sensitive or destructive                                     |
| **Accept edits** | allows file writes and edits without pausing; other protected actions remain subject to HITL |
| **Autonomous**   | does not pause for ordinary human approval; policy and security blocks remain active        |
| **Plan**         | pauses for the first protected action in a turn; each metered media call needs its own approval |
| **Bypass**       | disables ordinary HITL pauses; independent guardrails still apply                           |

These are permission modes, not trust levels for the model. Some operations, such as desktop control, can still require confirmation in every mode. In **Plan**, an approval already granted in the same turn lets the plan continue; it is not a permanent authorization.

A pre-approval is a separate, explicit grant. It can be consumed once for one tool and its exact arguments, within the same workspace and `run_id`, and expires after a limited time. Irreversible tools cannot use this grant.

In **Autonomous** mode, a Decision Model participates only when enabled, selected, ready, and allowed for the action's surface. Without it, no additional automated decision is assumed: static policy still controls what can run. If an evaluation that has started fails, times out, or requires human review, the action is not treated as approved.

See [Orchestrator & Subagents](../../concepts/sub-agents) for the HITL flow and [Decision models](../decision-models) to configure providers and surfaces.

## Orchestrator thinking

The "thinking" block shows the orchestrator's decision before acting: answer directly or delegate to `coder`/`search`, and why. This is real transparency, not just a spinner — you can see the reasoning, not just wait for the result.

## Memory across sessions

A "🧠 N memories loaded" badge appears when the agent uses persistent memories from previous conversations in that response. Manage memories manually in **Settings → Preferences → Memory**.

## RAG citations

Answers based on indexed content bring clickable `[1] [2]` citations — click to see the original excerpt and source.

## Multi-user (Pro)

In Pro mode with multi-user web chat, threads can be shared between members of the same workspace, with RBAC controlling who sees what.

## See also

- [Using the workbench](../using-the-workbench) — the side panel that accompanies chat
- [Using settings](../using-settings) — where the default model, language, and theme live
