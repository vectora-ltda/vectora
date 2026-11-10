---
title: Tools Reference
weight: 3
---

Vectora ofrece herramientas nativas organizadas por categoría, además de conexiones a servidores MCP configurados por el usuario. La lista de herramientas disponibles puede variar según la versión, los permisos y las integraciones habilitadas.

| Categoría          | Herramientas                                                                                                    |
| -------------------- | -------------------------------------------------------------------------------------------------------------------- |
| **Archivos**        | `file_read`, `file_edit`, `file_write`, `grep`, `list_dir`, `terminal`, `create_artifact`                             |
| **Git**             | Operaciones para status, log, diff, branch, checkout, commit, push, pull, stage/unstage, stash, merge, compare        |
| **GitHub**          | `gh_issue_create/list/view/comment`, `gh_pr_create/list/view/merge` (vía la CLI `gh`)                                 |
| **Web**             | `web_search`, `web_fetch` + extracción/crawl                                                                          |
| **RAG**             | `vector_search`, `embedding`, `ingest_docs`, `manage_retriever`                                                       |
| **Context Graph**   | `build_knowledge_graph`, `graph_query`, `graph_explain`, `graph_path`, `graph_update`, `graph_affected`               |
| **Memoria**         | `save_memory`, `get_memory`, `delete_memory`                                                                          |
| **Workspace**       | `workspace_list`, `workspace_describe`, `bucket_summary`                                                              |
| **Integraciones**   | Jira, Slack, Linear, Google Drive, Gmail, Notion (opcional, vía OAuth/API key por workspace)                          |
| **Utilidad**        | `time_*`, `hash_*`, `base64_*`, `regex_test`, `json_query`, `jwt_decode`, `http_request`                              |
| **MCP**             | `call_mcp_tool` — delega a servidores MCP de terceros configurados por el usuario                                     |

## Habilitar/deshabilitar herramientas

Los administradores controlan qué herramientas están disponibles para cada usuario en **Configuración → Administración → Herramientas**. Cada plugin MCP de terceros tiene su propio panel de **Política de Herramientas** (en **Configuración → Entorno → Plugins**) para granularidad por servidor.

## Defensividad

Toda herramienta tiene manejo interno de errores — un fallo de herramienta nunca hace crashear la conversación; se convierte en una observación de error que el propio agente ve y puede reaccionar ante ella (reintentar, notificarte, elegir otro camino).

## Ver también

- [Orchestrator & Subagents](../../concepts/sub-agents) — cómo coder/search usan este inventario
- [Usando la configuración](../../guides/using-settings) — la pestaña Herramientas
