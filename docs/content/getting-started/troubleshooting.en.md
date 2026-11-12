---
title: Troubleshooting
weight: 6
---

## The model selector is empty or chat fails to start

Configure an available chat provider and model in **Settings → Environment → AI Models**. Open **Provider Routing** to configure provider credentials and endpoints. Tab names may vary by version; see [Model configuration](../../guides/using-settings) as well. Embeddings, reranking, and web search are not required to start a regular chat.

## Project search or indexing returns no results

Confirm that the workspace and its files are included in indexing and that the configured embedding provider is available. Check the workbench's **Memory** tab and backend logs for indexing, embedding, or reranking failures. These capabilities may use different providers from chat. See [RAG](../../concepts/rag) and [Storage](../../concepts/storage).

## The agent cannot write files or run commands

Check whether the workspace is trusted and whether the permission mode, tool policy, and sandbox allow the operation. Tool access may require HITL approval. Do not trust a folder whose origin you do not know. See [First workspace](../first-workspace) and [Security](../../security/).

## Tools from an MCP server are missing

Check the connection status in **Settings → Environment → Plugins**. Verify the command, arguments, transport, and connector logs. See the [MCP client reference](../../reference/mcp-client).

## Complete storage mode cannot connect

`complete` mode requires PostgreSQL, Qdrant, and Redis to be available at the configured URLs (`POSTGRES_DSN`, `QDRANT_URL`, `REDIS_URL`). For source development, see the `scons docker` command in the [Docker guide](../../deployment/docker). Local `lite` mode does not require these services.

## Getting more details

Local logs are stored in `~/.vectora/logs/`. Remove or mask tokens, private paths, and conversation content before sharing a log excerpt.

## Report a problem

First search [existing issues](https://github.com/vectora-company/vectora/issues). For a vulnerability, follow the [security policy](https://github.com/vectora-company/vectora/blob/master/SECURITY.md) instead of publishing details in an issue.
