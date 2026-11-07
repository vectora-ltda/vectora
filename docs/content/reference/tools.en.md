---
title: Tools Reference
weight: 3
---

Vectora provides native tools organized by category, as well as connections to user-configured MCP servers. The available tool list can vary by version, permissions, and enabled integrations.

| Category          | Tools                                                                                                         |
| ----------------- | ------------------------------------------------------------------------------------------------------------- |
| **Files**         | `file_read`, `file_edit`, `file_write`, `grep`, `list_dir`, `terminal`, `create_artifact`                     |
| **Git**           | Operations for status, log, diff, branch, checkout, commit, push, pull, stage/unstage, stash, merge, compare |
| **GitHub**        | `gh_issue_create/list/view/comment`, `gh_pr_create/list/view/merge` (via the `gh` CLI)                        |
| **Web**           | `web_search`, `web_fetch` + extraction/crawl                                                                  |
| **RAG**           | `vector_search`, `embedding`, `ingest_docs`, `manage_retriever`                                               |
| **Context Graph** | `build_knowledge_graph`, `graph_query`, `graph_explain`, `graph_path`, `graph_update`, `graph_affected`       |
| **Memory**        | `save_memory`, `get_memory`, `delete_memory`                                                                  |
| **Workspace**     | `workspace_list`, `workspace_describe`, `bucket_summary`                                                      |
| **Integrations**  | Jira, Slack, Linear, Google Drive, Gmail, Notion (optional, via OAuth/API key per workspace)                  |
| **Utility**       | `time_*`, `hash_*`, `base64_*`, `regex_test`, `json_query`, `jwt_decode`, `http_request`                      |
| **MCP**           | `call_mcp_tool` — delegates to third-party MCP servers configured by the user                                 |

## Enabling/disabling tools

Administrators control which tools are available to each user in **Settings → Administration → Tools**. Each third-party MCP plugin has its own **Tool Policy** panel (in **Settings → Environment → Plugins**) for per-server granularity.

## Defensiveness

Every tool has internal error handling — a tool failure never crashes the conversation; it becomes an error observation that the agent itself sees and can react to (retry, notify you, choose another path).

## See also

- [Orchestrator & Subagents](../../concepts/sub-agents) — how coder/search use this inventory
- [Using settings](../../guides/using-settings) — the Tools tab
