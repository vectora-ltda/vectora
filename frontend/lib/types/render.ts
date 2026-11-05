/**
 * Tipos de renderização schema-driven — espelham os metadados das tools Python.
 *
 * O `render_hint` vem do campo `metadata={"render_hint": "..."}` de cada @tool.
 * O frontend usa o hint para decidir qual componente usar — sem hardcode por tool.
 */

/** Como o resultado de uma tool deve ser renderizado. */
export type RenderHint =
  | "diff" // file_edit — unified diff com cores
  | "code_block" // file_read, file_write, fetch_url — código com syntax highlight
  | "terminal_output" // terminal — fundo escuro, monospace
  | "search_results" // web_search, vector_search, search_memory — cards com score/fonte
  | "table" // grep, list_dir, manage_retriever, workspace_list — tabela paginada
  | "queue_progress" // ingest_docs — barra de progresso com contagem
  | "queue_badge" // embedding — badge com queue_id + status
  | "artifact" // create_artifact — card com ícone + link para download
  | "image_preview" // ferramentas que retornam URL de imagem
  | "browser_screenshot" // screenshot de browser — usa o mesmo ImagePreview
  | "thinking_step" // sequential_thinking — accordion com passo de raciocínio
  | "json_tree" // árvore JSON colapsável interativa
  | "chart_inline" // gráfico de barras SVG embutido (labels + values)
  | "db_result" // resultado de query SQL — tabela com colunas + linhas
  | "json"; // save_memory, get_memory, delete_memory, fallback universal

/** Categoria de uma tool — agrupa tools afins na UI. */
export type ToolCategory =
  | "filesystem"
  | "web"
  | "rag"
  | "memory"
  | "workspace"
  | "mcp"
  | "artifacts"
  | "general";
