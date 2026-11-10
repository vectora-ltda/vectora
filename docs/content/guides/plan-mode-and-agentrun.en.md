---
title: Plan Mode, AgentRun, and decisions
weight: 5
---

This guide explains how to plan a task before running it, how to follow each agent run through AgentRun, and how approval decisions are recorded.

> **Status:** these capabilities are included in version 0.1.23. Live tests marked `skipped` do not prove behavior in a real environment.

## Prerequisites

- A workspace open in Vectora.
- A model provider configured for chat.
- For Decision Models: a configured decision provider (see [Decision models](../decision-models)).

Check the prerequisites above before configuring planning and run tracking.

## Configuration

This section covers how to start Plan Mode, what an AgentRun records, and which decisions are stored with each run.

### Plan Mode

Plan Mode can be used in two ways:

- **`/plan` prefix:** a message that starts with `/plan` is sent in planning mode. The prefix is removed before the text reaches the agent.
- **`Plan` permission mode:** the agent pauses at the first protected action of the turn and asks for your approval. Once you approve, the plan continues in the same turn. The approval does not carry over to later turns. With confirmed native worker isolation, `should_require_approval` exempts `terminal`, `file_write` and `file_edit`; even the first such action can run without human approval in Plan permission mode. Other policy and tool restrictions still apply.

Plan Mode is not the workbench **Plan** tab. The tab shows the session's artifacts and checklist; Plan Mode controls when the agent pauses. See [Using the chat](../using-the-chat) for all permission modes.

A sandbox policy being enabled is not sufficient to waive human approval: the worker must already be alive with effective native isolation. Until that is confirmed, and in soft mode, the normal HITL rules apply. A sandbox-exempt tool result never satisfies the Plan approval gate for a later protected action; only an audited human approval in the same run and user turn does.

### AgentRun

Every agent run has a `run_id`, a state, evidence, and the relationships between the main agent and its subagents. The thread history is derived from an idempotent event ledger.

The possible states are:

| State                                       | Meaning                                                              |
| ------------------------------------------- | -------------------------------------------------------------------- |
| `planned`, `admitted`                       | The run was created and accepted but has not started                 |
| `running`, `tool_running`                   | The agent is generating or running a tool                            |
| `waiting_approval`                          | Waiting for a human decision                                         |
| `waiting_provider`                          | Waiting for the model provider                                       |
| `verifying`                                 | Running the workspace quality gates                                  |
| `passed`, `degraded`, `failed`, `cancelled` | Final states                                                         |
| `recovery_required`                         | The run was interrupted and needs reconciliation before it continues |

When authentication is enabled, the following routes require authentication; with authentication disabled, local mode uses a virtual local user:

- `GET /runs/{run_id}`: state and evidence of a run.
- `GET /runs/{run_id}/timeline`: the run's event timeline.
- `GET /threads/{thread_id}/runs`: the runs of a thread.

In local mode, runs are stored in SQLite. Installations using PostgreSQL follow the same contract.

### Recorded decisions

Each run records three kinds of decisions: human approvals, MCP server access, and Decision Model evaluations.

#### Human approvals (HITL)

When the agent pauses, you answer with one of these decisions:

| Decision         | Effect                                                        |
| ---------------- | ------------------------------------------------------------- |
| `approve`        | Runs the proposed action                                      |
| `reject`         | Does not run it; the agent receives the refusal as the result |
| `option:<value>` | Picks one of the offered options                              |
| `edit:<json>`    | Runs it with the edited arguments                             |

The decision is linked to the run's `run_id`.

#### Access to MCP servers

Before using an MCP server, Vectora evaluates the MCP policy and records the outcome:

| Code                 | Meaning                                          |
| -------------------- | ------------------------------------------------ |
| `allowed`            | The server is allowed (or no rule applies)       |
| `blocked`            | The server is not on the applicable allowlist    |
| `policy_unavailable` | The policy could not be loaded; access is denied |

The workspace rule takes precedence over the instance rule. The MCP allowlist controls which servers can be used; it is not an egress policy (see [Egress policies](../egress-policies)).

#### Decision Models

With the configurable HITL layer, a Decision Model can evaluate an action and return `allow`, `deny`, or `requires_human`. A decision never widens permissions: policy, gates, and local controls still apply. See [Decision models](../decision-models).

## Limitations

- A `Plan` mode approval only applies to the turn in which it was given.
- Operations such as desktop control may require confirmation in any permission mode.
- A run in `recovery_required` does not automatically repeat tools that may already have produced effects.
- Decision Models need a configured, ready provider; an unavailable provider does not count as an approval.
