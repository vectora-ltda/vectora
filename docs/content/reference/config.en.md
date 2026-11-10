---
title: Configuration (Reference)
weight: 2
---

## `~/.vectora/config.toml`

Runtime configuration, generated/edited by `vectora config` or the Settings UI. Minimal example:

```toml
[global]
mode = "desktop"   # or "cli", "headless", "mcp", "web"

[runtime]
auto_update = true

[server]
host = "0.0.0.0"
port = 8080
```

## Main environment variables

| Variable                                                  | Required?           | Description                                                  |
| --------------------------------------------------------- | ------------------- | ------------------------------------------------------------ |
| `LLM_PROVIDER`                                            | No (defaults to `google-genai`) | Selects the chat provider; configure its matching credentials |
| `GOOGLE_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Depends on provider | the chosen LLM provider's key                                |
| `COHERE_API_KEY`                                          | Optional            | Cohere credentials for features configured to use it        |
| `TAVILY_API_KEY`                                          | Optional            | web search                                                   |
| `STORAGE_MODE`                                            | No (default `lite`) | `lite` or `complete` — see [Storage](../../concepts/storage) |
| `POSTGRES_DSN`                                            | Only in `complete`  | e.g. `postgresql+asyncpg://user:pass@host:5432/vectora`      |
| `QDRANT_URL` / `QDRANT_API_KEY`                           | Only in `complete`  | Qdrant endpoint                                              |
| `REDIS_URL`                                               | Only in `complete`  | Redis endpoint                                               |
| `TELEMETRY_ENABLED`                                       | No (default `true`) | Enables native agent telemetry (turn start/end, tool calls, unhandled errors) via structured logging |
| `TELEMETRY_OUTPUT_PATH`                                   | Optional            | Path to a dedicated JSONL file for telemetry events. If unset, events go to the standard JSON log (`~/.vectora/logs/backend.jsonl`) |

## Hierarchy

```text
defaults.env (built-in)  →  .env (project)  →  ~/.vectora/.env (user)  →  per-user overrides (database)
```

See [Configuration](../../getting-started/configuration) for the full usage guide.
