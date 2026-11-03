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

## Changes during an execution

Decision preferences are recorded per execution. Changing the provider, enabling or disabling the model, or changing its surfaces invalidates the previous authorization. Returning to the original configuration does not restore that authorization: a new evaluation or approval is required according to policy. Authorization is checked again before invoking the tool, including after asynchronous preparation.

## Local transcription

Transcription and runtime probes share a limit of one native operation per process. Wait for an ongoing operation to finish before retrying. Transcription waits up to 60 seconds; the probe waits up to 45 seconds. A timeout does not terminate native inference: the slot remains occupied and temporary audio is removed when processing finishes. A late result is never reported as a successful probe after a timeout.

## Recording and replaying an execution

Enable `EVALUATION_RECORDING_ENABLED=true` before starting the backend to capture actual model inputs. Recording is optional and disabled by default; it stores context and responses in AgentRun with credential redaction. Content can still contain private information: apply the same access and retention controls as conversation history.

Save the JSON response from the authenticated `GET /runs/{run_id}` query as `run.json`, then export and replay it:

```shell
vectora eval export-run --run-file run.json --target run.cassette.json
vectora eval run --suite run.cassette.json --output json
```

The export links interactions to decisions and observed tool-result digests. Pending operations are marked as incomplete outcomes. Replay uses recorded responses without calling providers or executing tools again. It does not prove live availability or reproducibility of the external environment.

The **AI models** tab also includes the primary chat model selector and a dedicated image understanding and vision fallback section. Chat fallback order remains separate from image, TTS and video generation.

Evaluation recordings live in AgentRun and are removed with the session. Exported JSON files are independent copies: apply your environment’s retention policy and delete those files separately when they are no longer needed.
