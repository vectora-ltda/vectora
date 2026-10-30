---
title: Decision models
weight: 2
---

Decision Models evaluate structured runtime decisions, such as the risk of a tool call. They do not replace tool permissions, workspace policy, or approval gates.

## Configuration

In **Settings → AI Models**, select an available provider, configure the required credentials, and enable Decision Models. The surface matrix controls where a model may be consulted, including HITL, subagents, Plan Mode, Browser, MCP, terminal, network, and background tasks.

Preferences are associated with the user and active workspace. When switching workspaces, check the selected provider and enabled surfaces again. A disabled surface must not be authorized by another surface's preference.

## How a decision affects an action

1. The runtime checks the configuration and applicable surface.
2. If evaluation is enabled and the provider is ready, the model returns a structured decision.
3. Policy and local controls remain authoritative. A model may require more approval or block an action; its response cannot grant permissions denied by policy.
4. If an evaluation that has started times out, fails, returns invalid content, or requires human review, the action is not treated as approved.
5. If the Decision Model is disabled or unavailable before evaluation starts, no additional automated decision is assumed; static runtime gates remain in force.

## Limits

- Enabling the feature does not make every provider available. Credentials, health, local dependencies, and connectivity must work in the current environment.
- A selected option in the interface indicates a preference. It does not prove that a provider responded or that an evaluation ran.
- Unit and integration tests do not prove live availability of Clef, Laya, or JEV. Results marked `skipped` must remain identified as not executed.
- Decision Models do not replace human confirmation when policy or the result requires approval.

## Security and data

Decisions are associated with their execution context. Do not place credentials or private content in decision fields. The interface should mask secrets, and audit records should store only the metadata and summaries needed to explain a decision.
