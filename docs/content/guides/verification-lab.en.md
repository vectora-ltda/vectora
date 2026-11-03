---
title: Verification and Evaluation Lab
weight: 9
---

This guide shows how to configure the quality gates that check a run's result and how to use the Evaluation Lab to record, replay, and compare runs.

> **Status:** these capabilities are part of the 0.3 line. This page does not claim that live tests still marked as `skipped` are complete.

## Prerequisites

- A workspace open in Vectora.
- The commands used by the gates (for example `pytest`, `npm test`, or a linter) installed in the workspace environment.
- For the Evaluation Lab: the `vectora` CLI available in your terminal.

Configure each layer according to the operations you need to restrict.

## Configuration

Verification is configured per workspace in a profile file; the Evaluation Lab is used from the CLI.

### Quality gates

After a run, Vectora runs the gates from the workspace verification profile. The profile is stored at `verification/<workspace_id>.json` inside the Vectora home (`~/.vectora` by default, or the directory set in `VECTORA_HOME`).

Without a saved profile, the workspace uses an active default profile with no gates: no check blocks the run.

#### Profile fields

| Field | Description |
| --- | --- |
| `enabled` | Turns automatic verification on or off |
| `gates` | List of gates (see below) |
| `require_clean_git` | Requires a clean working tree after the run |
| `inject_stack_trace_on_failure` | Feeds the error output back into the next turn so the agent can fix it |

Each gate has:

| Field | Description |
| --- | --- |
| `id`, `name` | Identifier and display name |
| `command` | Command and arguments, as a list (cannot be empty) |
| `policy` | `block` (fails the run), `degrade` (marks it degraded), or `inform` (warns only); default `block` |
| `file_patterns` | Glob patterns of the files that trigger the gate |
| `timeout_seconds` | Maximum time, from 5 to 3600 seconds; default 300 |

Example:

```json
{
  "workspace_id": "my-workspace",
  "enabled": true,
  "gates": [
    {
      "id": "pytest",
      "name": "Python tests",
      "command": ["pytest", "-q"],
      "policy": "block",
      "file_patterns": ["**/*.py"],
      "timeout_seconds": 600
    }
  ],
  "require_clean_git": false,
  "inject_stack_trace_on_failure": true
}
```

#### Results

Every verification ends in one of these results: `passed`, `failed`, `infra_error`, `not_run`, `blocked`, `degraded`, or `unverified`. A test that did not run (`not_run`) is different from a test that passed. Each gate also reports a reason: `passed`, `assertion_failed`, `timeout`, or `dispatch_error`.

If the profile file is corrupted, Vectora applies a protective gate that fails with the `block` policy instead of skipping verification.

### Evaluation Lab

The Evaluation Lab replays existing cassettes without calling the external provider. The CLI does not directly capture live interactions. Enable `EVALUATION_RECORDING_ENABLED=true` before a run to record its model interactions in AgentRun. Save the authenticated `GET /runs/{run_id}` JSON response, then use `vectora eval export-run --run-file run.json --target cassette.json`; it calls `cassette_from_run` and rejects runs without recorded interactions. Deleting the session removes its stored trace, but exported files require separate retention and deletion. Only known credential fields and patterns are hidden; messages and responses can retain private code or personal data. Restrict access and review cassettes before sharing.

```bash
vectora eval export-run --run-file run.json --target cassette.json
vectora eval run --suite path/to/suite.json
vectora eval compare baseline.json candidate.json
```

- `vectora eval run` replays a single cassette (`--suite` points to one cassette file; a list of cassettes is rejected).
- `vectora eval compare` compares two cassettes (baseline and candidate).
- Both commands accept `--output human` (default) or `--output json`.

Review the boundaries below before relying on replay or sharing a recording.

## Limitations

- Replay reproduces a recorded cassette; it does not prove that the provider, the Browser Workbench, or another integration is available live.
- Live validations marked as `skipped` do not count as approval.
- Gates run in the workspace environment; a missing command (`dispatch_error`) or a timeout produces the `infra_error` outcome with a `failed` terminal status; it is not reported as an `assertion_failed` test result.
