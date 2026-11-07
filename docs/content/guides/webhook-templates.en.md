---
title: Webhook Templates
weight: 8
---

A `webhook`-triggered background task turns an external event into an agent run: a provider (GitHub, GitLab, Slack, Linear, email, or your own alerting tool via the [observability endpoint](../observability-webhooks)) posts to Vectora, and — if a matching task exists — the agent wakes up with that event's payload embedded in its instruction. This page is the reference for the three concrete models Vectora ships today, in increasing order of what the agent actually does with the event.

This capability requires a **Pro** entitlement (chat automation triggered from outside the product) — see [entitlements and pricing](https://vectora.company/#pricing).

## How the bridge works

1. The provider posts to `POST /webhook/{provider}` (or `POST /webhook/observability` for the generic alerting contract). The signature is verified against a secret env var (`GITHUB_WEBHOOK_SECRET`, `GITLAB_WEBHOOK_TOKEN`, etc.) before anything else happens.
2. Vectora looks for **enabled** background tasks with `trigger_type: "webhook"` whose `trigger_config` matches the event — `{"provider": "github", "events": ["pull_request"]}`, for example. Multiple matching tasks all fire; none matching means the event is persisted (visible in the workbench event stream) but nothing runs.
3. Each matching task's session and workspace become the home for that run — the agent has that session's full history and that workspace's filesystem/git/tools available, same as any other run.
4. The event payload (truncated to 4000 characters) is appended to the task's instruction as a JSON block, so the agent reads it directly rather than through a separate structured field.

You create the task the same way you create any other background task: ask the agent in chat. There's no separate webhook-config form — `trigger_config` is just JSON the agent fills in from what you tell it.

## Model 1 — GitHub PR review (deterministic tools, LLM judgment)

The flagship model: the agent reads a PR's real diff and posts a review comment, entirely on its own.

**Setup**: in chat, ask something like:

> "Create a background task, triggered by GitHub `pull_request` webhooks, that fetches the PR diff and posts a short review comment — flag anything that looks unsafe or untested, otherwise say it looks fine."

This creates a task with `trigger_type: "webhook"`, `trigger_config: {"provider": "github", "events": ["pull_request"]}`. Configure your GitHub repo's webhook (**Settings → Webhooks → Add webhook**) to point at `https://<your-backend>/webhook/github` with content type `application/json`, and set `GITHUB_WEBHOOK_SECRET` on the backend to match what you enter as the webhook's secret.

**What runs**: the agent has two tools available for this — `github_fetch_pr_diff(owner, repo, pr_number)`, which retrieves the unified diff (the webhook payload only carries metadata and URLs, not the diff itself), and `github_post_pr_comment(owner, repo, pr_number, body)`, which posts the review as an issue comment (PRs are issues under the hood on GitHub, so this is the same endpoint). Both use the `GITHUB_TOKEN` already configured through the GitHub OAuth/PAT integration — no separate credential. Either tool failing (repo renamed, token missing the `repo` scope, PR force-closed mid-run) returns a typed error the agent sees and can react to instead of a crash; a failed diff fetch means the agent won't attempt the comment.

## Model 2 — GitHub Issues → Kanban (deterministic, no LLM)

Every `opened`/`closed`/`reopened`/`edited`/`assigned` event on a GitHub issue mirrors into a Kanban card — title, state, and a link back to the issue — without invoking the LLM at all. This is a fixed insert-or-update against the same task table the rest of the Kanban model uses, keyed by `repo` + `issue_number`, so redelivery of the same event updates the existing card instead of duplicating it.

**Setup**: create a task with `trigger_config: {"provider": "github", "events": ["issues"]}`. That task's session and workspace become the home for every issue-derived card. Without such a task enabled, issue events are still received and persisted, but no card is created.

## Model 3 — Observability alerts → Kanban (deterministic, no LLM)

Same mechanism as Model 2, but for alerting tools (Sentry, Grafana, PagerDuty, or anything that can send a webhook) via the dedicated `/webhook/observability` endpoint and its fixed payload contract. Severity maps to the card's initial column — `critical`/`high` lands in triage, everything else in todo. Full field reference, authentication details, and provider-specific setup (Sentry, Grafana, PagerDuty) live in [Observability Webhooks](../observability-webhooks).

## See also

- [Observability Webhooks](../observability-webhooks) — full payload contract and provider setup for Model 3
- [Agent Automation](../agent-automation) — scheduling, delegation, and Vectora Connect
- [Using the Workbench](../using-the-workbench) — the Tasks tab where these runs show up
