---
title: Quick start
weight: 3
---

This guide covers first use after installation. To build the project, see [Installation](../installation).

## 1. Start Vectora and configure a model

Open the app. On a new local instance, create the initial account and follow the setup flow. Then open **Settings → AI Models** to choose default models and configure the providers you need. Use **Provider Routing** for endpoints and routing.

Configure only the capabilities you intend to use. A remote provider may receive the requests and data required for a task; local models must be installed and running in the backend environment. See [Settings](../../guides/using-settings) and [BYOK and privacy](../../security/byok-privacy).

## 2. Open a workspace

Use the workspace selector to add your project folder. A workspace is the context boundary for files, terminal, Git, search, and agent tools.

A new workspace starts untrusted. Read the warning and trust it only if you know its origin and accept that the agent may perform operations allowed by your configuration. See [First workspace](../first-workspace).

## 3. Try a small request

Start with a question whose answer you can verify:

```text
Summarize this project's structure and point to the files you used as evidence.
```

Then try a small change on a working branch. The agent may use tools or delegate tasks; permissions, policies, and HITL gates determine whether an action needs confirmation.

## 4. Explore project retrieval

Open the workbench's **Memory** tab to inspect the knowledge base and retrieved results. Indexing depends on accepted files and configured providers; verify the result before assuming an entire folder was indexed. [RAG and Context Graph](../../concepts/rag) explains context sources and limits.

## Next steps

- [Using the chat](../../guides/using-the-chat)
- [Using settings](../../guides/using-settings)
- [Decision Models](../../guides/decision-models)
- [Troubleshooting](../troubleshooting)
