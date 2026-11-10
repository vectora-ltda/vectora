---
title: RAG and project context
weight: 1
---

RAG (Retrieval-Augmented Generation) lets the agent retrieve passages from indexed documents to answer with workspace context. Retrieval depends on the included files, indexing queue state, selected storage, and configured embedding/reranking providers.

## General flow

```text
selected files and documents
             │
             ▼
       extraction and chunking
             │
             ▼
       embeddings and index
             │
             ▼
    relevant passage retrieval
             │
             ▼
 context sent to the selected model
```

Vectora combines lexical and vector retrieval when those capabilities are available in the configuration. Reranking is configurable and may use a compatible provider. Web search is a separate tool and should not be confused with local retrieval. A remote provider may receive the query and passages needed for a request; review its data policy before using it with confidential content.

## Embeddings and reranking

Choose providers and models in **Settings → Environment → AI Models**; configure endpoints and credentials in **Provider Routing**. Options include compatible remote providers and local configurations. Vectora does not require a Cohere key to start chat. Indexing needs a working embedding provider, while reranking can be configured separately or turned off.

## Querying the index

Use the workbench's **Memory** tab to follow retrieval activity and adjust available RAG settings. Include the needed files in the workspace, wait for indexing to finish, and inspect retrieved sources before assuming the entire project was indexed.

## Limits

RAG retrieves passages from an index; it does not guarantee completeness, freshness, or correctness. Excluded files, extraction errors, context limits, and provider settings affect results. Verify important claims against the original source.

## See also

- [Context Graph](../context-graph) — structural relationships extracted from code
- [Using the workbench](../../guides/using-the-workbench) — the Memory panel in practice
- [BYOK & Privacy](../../security/byok-privacy) — data flow to external providers
