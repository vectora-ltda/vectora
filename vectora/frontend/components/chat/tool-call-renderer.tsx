/**
 * ToolCallRenderer — renderização schema-driven de tool calls.
 *
 * Despacha para o renderer correto com base no `renderHint` da tool.
 * O hint vem do campo `metadata={"render_hint": "..."}` no Python — sem
 * hardcode por nome de tool.
 *
 * Hierarquia de renderização:
 *   1. renderHint presente → renderer específico
 *   2. fallback → JsonViewer
 */

"use client";

import { memo } from "react";
import { Download, Loader2 } from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";
import { m } from "@/lib/paraglide/messages";
import type { ToolCall, RenderHint } from "@/lib/types";
import { languageFromPath } from "@/lib/monaco/languages";
import { MonacoCodeBlock } from "@/components/workbench/monaco-code-block";

// ============================================================================
// Props
// ============================================================================

interface ToolCallRendererProps {
  tool: ToolCall;
  /** Se o output ainda está sendo preenchido */
  isStreaming?: boolean;
}

// ============================================================================
// Renderers individuais
// ============================================================================

/** Fallback universal — JSON colapsável */
function JsonViewer({ data, label }: { data: unknown; label: string }) {
  const text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  return (
    <details className="mt-1">
      <summary className="cursor-pointer text-[11px] text-muted-foreground hover:opacity-80">
        {label}
      </summary>
      <pre className="mt-1 text-[10px] font-mono whitespace-pre-wrap break-words max-h-48 overflow-y-auto rounded bg-muted/60 p-2">
        {text}
      </pre>
    </details>
  );
}

/** Diff colorido — file_edit */
function DiffViewer({ content }: { content: string }) {
  const lines = content.split("\n");
  return (
    <div className="mt-1 font-mono text-[11px] rounded border border-border overflow-hidden max-h-64 overflow-y-auto">
      {lines.map((line, i) => {
        const bg = line.startsWith("+")
          ? "bg-green-500/10 text-green-400"
          : line.startsWith("-")
            ? "bg-red-500/10 text-red-400"
            : line.startsWith("@@")
              ? "bg-blue-500/10 text-blue-300"
              : "text-muted-foreground";
        return (
          <div
            key={i}
            className={`px-3 py-px whitespace-pre-wrap break-all ${bg}`}
          >
            {line || " "}
          </div>
        );
      })}
    </div>
  );
}

/** Bloco de código com syntax highlight — file_read, file_write, fetch_url */
function CodeBlockViewer({ content, name }: { content: string; name: string }) {
  const engineLanguage = languageFromPath(name);
  if (
    ["gdscript", "godot-resource", "godot-project", "gdshader"].includes(
      engineLanguage,
    )
  ) {
    return (
      <div className="mt-1 max-h-60 overflow-auto rounded border border-border bg-muted/40 p-3 font-mono text-[11px]">
        <MonacoCodeBlock code={content} language={engineLanguage} />
      </div>
    );
  }
  const ext = name.split(".").pop() ?? "text";
  const langMap: Record<string, string> = {
    py: "python",
    ts: "typescript",
    tsx: "tsx",
    js: "javascript",
    jsx: "jsx",
    json: "json",
    md: "markdown",
    yml: "yaml",
    yaml: "yaml",
    toml: "toml",
    sh: "bash",
    bash: "bash",
    html: "html",
    css: "css",
    sql: "sql",
    rs: "rust",
    go: "go",
    java: "java",
    cpp: "cpp",
    c: "c",
  };
  const lang = langMap[ext] ?? "text";
  return (
    <SyntaxHighlighter
      language={lang}
      style={vscDarkPlus}
      customStyle={{
        margin: "4px 0 0",
        fontSize: "11px",
        borderRadius: "6px",
        maxHeight: "240px",
      }}
    >
      {typeof content === "string" ? content : JSON.stringify(content, null, 2)}
    </SyntaxHighlighter>
  );
}

/** Saída de terminal — fundo preto */
function TerminalBlock({ content }: { content: string }) {
  return (
    <pre className="mt-1 text-[11px] font-mono bg-zinc-950 text-zinc-200 rounded p-3 max-h-52 overflow-y-auto whitespace-pre-wrap break-words border border-zinc-800">
      {typeof content === "string" ? content : JSON.stringify(content, null, 2)}
    </pre>
  );
}

/** Cards de resultados de busca — web_search, vector_search, search_memory */
function SearchResultsViewer({ results }: { results: unknown }) {
  let items: Array<{
    title?: string;
    url?: string;
    content?: string;
    key?: string;
    score?: number;
  }> = [];
  try {
    const parsed = typeof results === "string" ? JSON.parse(results) : results;
    items = Array.isArray(parsed)
      ? parsed
      : (parsed?.memories ?? parsed?.results ?? []);
  } catch {
    return <JsonViewer data={results} label="Ver resultados" />;
  }

  if (!items.length) {
    return (
      <p className="text-[11px] text-muted-foreground mt-1 italic">
        Nenhum resultado.
      </p>
    );
  }

  return (
    <div className="mt-1 space-y-1.5 max-h-52 overflow-y-auto">
      {items.slice(0, 8).map((item, i) => (
        <div
          key={i}
          className="rounded border border-border bg-muted/30 px-2 py-1.5 text-[11px]"
        >
          <div className="font-medium text-foreground truncate">
            {item.title ?? item.key ?? `Resultado ${i + 1}`}
          </div>
          {item.url && (
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-400 hover:underline truncate block text-[10px]"
            >
              {item.url}
            </a>
          )}
          {item.content && (
            <p className="text-muted-foreground mt-0.5 line-clamp-2">
              {item.content}
            </p>
          )}
          {item.score != null && (
            <span className="text-[10px] text-muted-foreground">
              score: {item.score.toFixed(3)}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

/** Tabela — grep, list_dir, manage_retriever, workspace_list */
function TableViewer({ data }: { data: unknown }) {
  let rows: Array<Record<string, unknown>> = [];
  let text = "";
  try {
    const parsed = typeof data === "string" ? JSON.parse(data) : data;
    if (
      Array.isArray(parsed) &&
      parsed.length &&
      typeof parsed[0] === "object"
    ) {
      rows = parsed;
    } else if (typeof parsed === "string") {
      text = parsed;
    } else {
      text = typeof data === "string" ? data : JSON.stringify(parsed, null, 2);
    }
  } catch {
    text = typeof data === "string" ? data : "";
  }

  if (text) {
    return (
      <pre className="mt-1 text-[11px] font-mono bg-muted/40 rounded p-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-words">
        {text}
      </pre>
    );
  }

  if (!rows.length)
    return (
      <p className="text-[11px] text-muted-foreground mt-1 italic">
        Sem dados.
      </p>
    );

  const cols = Object.keys(rows[0]);
  return (
    <div className="mt-1 overflow-x-auto max-h-48">
      <table className="text-[11px] border-collapse w-full">
        <thead>
          <tr>
            {cols.map((c) => (
              <th
                key={c}
                className="border border-border px-2 py-1 bg-muted/50 text-left font-medium"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 50).map((row, i) => (
            <tr key={i} className="even:bg-muted/20">
              {cols.map((c) => (
                <td
                  key={c}
                  className="border border-border px-2 py-0.5 truncate max-w-[200px]"
                >
                  {String(row[c] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Badge de status de fila — embedding */
function QueueBadge({ data }: { data: unknown }) {
  let info: Record<string, unknown> = {};
  try {
    info =
      typeof data === "string"
        ? JSON.parse(data)
        : ((data as Record<string, unknown>) ?? {});
  } catch {
    /* usa {} */
  }
  const status = String(info.status ?? info.queue_id ?? "enqueued");
  const qid = info.queue_id ? `#${String(info.queue_id).slice(0, 8)}` : "";
  return (
    <div className="mt-1 inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2.5 py-0.5 text-[11px] font-mono">
      <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
      <span>{status}</span>
      {qid && <span className="text-muted-foreground">{qid}</span>}
    </div>
  );
}

/** Barra de progresso de ingestão — ingest_docs */
function QueueProgress({ data }: { data: unknown }) {
  let info: Record<string, unknown> = {};
  try {
    info =
      typeof data === "string"
        ? JSON.parse(data)
        : ((data as Record<string, unknown>) ?? {});
  } catch {
    /* usa {} */
  }
  const total = Number(info.total ?? info.count ?? 0);
  const done = Number(info.queued ?? info.indexed ?? info.done ?? total);
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="mt-1 space-y-1">
      <div className="flex justify-between text-[10px] text-muted-foreground">
        <span>
          {info.message
            ? String(info.message)
            : `${done} / ${total || "?"} docs`}
        </span>
        {total > 0 && <span>{pct}%</span>}
      </div>
      {total > 0 && (
        <div className="h-1.5 rounded-full bg-muted overflow-hidden">
          <div
            className="h-full bg-primary rounded-full transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}

/** Imagem inline — browser_screenshot, image tools */
function ImagePreview({ data }: { data: unknown }) {
  let url = "";
  let alt = "";
  // `browser_screenshot` devolve a URL/data-URI crua como string; tools
  // como `generate_image` devolvem `json.dumps({...})` — uma string que
  // PARECE objeto. Sem distinguir os dois, `generate_image` nunca
  // renderizava (a string JSON inteira virava `src`, um `<img>` quebrado).
  let parsed: unknown = data;
  if (typeof data === "string" && data.trim().startsWith("{")) {
    try {
      parsed = JSON.parse(data);
    } catch {
      parsed = data;
    }
  }
  if (typeof parsed === "string") {
    url = parsed;
  } else if (parsed && typeof parsed === "object") {
    const d = parsed as Record<string, unknown>;
    url = String(d.url ?? d.src ?? d.image_url ?? "");
    alt = String(d.alt ?? d.description ?? "");
  }
  if (!url) return <JsonViewer data={data} label="Ver output" />;
  return (
    <img
      src={url}
      alt={alt}
      className="mt-1 max-w-full rounded border border-border"
      style={{ maxHeight: "240px" }}
    />
  );
}

/** Accordion de passo de raciocínio — sequential_thinking */
function ThinkingStep({ data }: { data: unknown }) {
  let d: Record<string, unknown> = {};
  try {
    d =
      (typeof data === "string"
        ? JSON.parse(data)
        : (data as Record<string, unknown>)) ?? {};
  } catch {
    /* usa {} */
  }
  const thought = String(d.thought ?? "");
  const num = Number(d.thought_number ?? 0);
  const total = Number(d.total_thoughts ?? 0);
  const isFinal = Boolean(d.is_final);
  const isRevision = Boolean(d.is_revision);
  return (
    <div className="mt-1 text-[11px] text-muted-foreground space-y-0.5">
      {(num > 0 || total > 0) && (
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] font-mono text-primary/60">
            {num}/{total}
          </span>
          {isFinal && (
            <span className="text-green-500 text-[10px]">✓ Final</span>
          )}
          {isRevision && (
            <span className="text-amber-500 text-[10px]">↩ Revisão</span>
          )}
        </div>
      )}
      <p className="whitespace-pre-wrap">{thought}</p>
    </div>
  );
}

/** Card de artifact — create_artifact */
function ArtifactCard({ data }: { data: unknown }) {
  let info: Record<string, unknown> = {};
  try {
    info =
      typeof data === "string"
        ? JSON.parse(data)
        : ((data as Record<string, unknown>) ?? {});
  } catch {
    /* usa {} */
  }
  const title = String(info.title ?? "Artifact");
  const path = String(info.path ?? "");
  const type = String(info.artifact_type ?? "");
  // `url` só existe pra mídia gerada pelo assistente (generate_video/
  // text_to_speech, via `media.py::_media_url`) — artifacts de markdown
  // (create_artifact) nunca tiveram e continuam mostrando só o path.
  const url = String(info.url ?? "");
  return (
    <div className="mt-1 rounded border border-border bg-muted/30 px-3 py-2 text-[12px]">
      <div className="font-semibold text-foreground">{title}</div>
      {type && (
        <div className="text-[10px] text-muted-foreground uppercase tracking-wide">
          {type}
        </div>
      )}
      {url ? (
        <a
          href={url}
          download
          className="mt-1 inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
        >
          <Download className="w-3 h-3" />
          {m.workbench_files_download()}
        </a>
      ) : (
        path && (
          <div className="text-[10px] font-mono text-muted-foreground mt-0.5 break-all">
            {path}
          </div>
        )
      )}
    </div>
  );
}

/** Árvore JSON colapsável — json_tree */
function JsonTree({ data }: { data: unknown }) {
  const text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  return (
    <details className="mt-1">
      <summary className="cursor-pointer text-[11px] text-muted-foreground hover:opacity-80">
        JSON
      </summary>
      <pre className="mt-1 text-[10px] font-mono whitespace-pre-wrap break-words max-h-64 overflow-y-auto rounded bg-muted/60 p-2">
        {text}
      </pre>
    </details>
  );
}

/** Gráfico de barras SVG embutido — chart_inline */
function ChartInline({ data }: { data: unknown }) {
  let labels: string[] = [];
  let values: number[] = [];
  let title = "";
  if (data && typeof data === "object") {
    const d = data as Record<string, unknown>;
    labels = Array.isArray(d.labels) ? (d.labels as string[]) : [];
    values = Array.isArray(d.values) ? (d.values as number[]) : [];
    title = String(d.title ?? "");
  }
  if (labels.length === 0 || values.length === 0) {
    return <JsonViewer data={data} label="Ver output" />;
  }
  const max = Math.max(...values, 1);
  const barW = 28;
  const gap = 6;
  const chartH = 80;
  const totalW = (barW + gap) * labels.length;
  return (
    <div className="mt-1 text-[11px]">
      {title && (
        <div className="text-muted-foreground mb-1 font-medium">{title}</div>
      )}
      <svg width={totalW} height={chartH + 20} role="img" aria-label={title}>
        {labels.map((lbl, i) => {
          const barH = Math.round((values[i]! / max) * chartH);
          const x = i * (barW + gap);
          return (
            <g key={lbl}>
              <rect
                x={x}
                y={chartH - barH}
                width={barW}
                height={barH}
                rx={2}
                className="fill-primary/70"
              />
              <text
                x={x + barW / 2}
                y={chartH + 12}
                textAnchor="middle"
                fontSize={9}
                className="fill-muted-foreground"
              >
                {lbl}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** Tabela de resultado SQL — db_result */
function DbResult({ data }: { data: unknown }) {
  let rows: Record<string, unknown>[] = [];
  let columns: string[] = [];
  if (data && typeof data === "object") {
    const d = data as Record<string, unknown>;
    rows = Array.isArray(d.rows) ? (d.rows as Record<string, unknown>[]) : [];
    columns = Array.isArray(d.columns)
      ? (d.columns as string[])
      : rows[0]
        ? Object.keys(rows[0])
        : [];
  }
  if (columns.length === 0)
    return <JsonViewer data={data} label="Ver output" />;
  return (
    <div className="mt-1 overflow-x-auto max-h-48">
      <table className="text-[11px] w-full border-collapse">
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col}
                className="border border-border bg-muted/50 px-2 py-1 text-left font-semibold"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className="odd:bg-muted/20">
              {columns.map((col) => (
                <td key={col} className="border border-border px-2 py-1">
                  {String(row[col] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ============================================================================
// Dispatch map
// ============================================================================

type RendererFn = (output: unknown, toolName: string) => React.ReactNode;

const RENDERERS: Record<RenderHint, RendererFn> = {
  diff: (out) => {
    const text = typeof out === "string" ? out : JSON.stringify(out, null, 2);
    return <DiffViewer content={text} />;
  },
  code_block: (out, name) => {
    const text = typeof out === "string" ? out : JSON.stringify(out, null, 2);
    return <CodeBlockViewer content={text} name={name} />;
  },
  terminal_output: (out) => <TerminalBlock content={out as string} />,
  search_results: (out) => <SearchResultsViewer results={out} />,
  table: (out) => <TableViewer data={out} />,
  queue_progress: (out) => <QueueProgress data={out} />,
  queue_badge: (out) => <QueueBadge data={out} />,
  artifact: (out) => <ArtifactCard data={out} />,
  image_preview: (out) => <ImagePreview data={out} />,
  browser_screenshot: (out) => <ImagePreview data={out} />,
  thinking_step: (out) => <ThinkingStep data={out} />,
  json_tree: (out) => <JsonTree data={out} />,
  chart_inline: (out) => <ChartInline data={out} />,
  db_result: (out) => <DbResult data={out} />,
  json: (out) => <JsonViewer data={out} label="Ver output" />,
};

// ============================================================================
// Helpers
// ============================================================================

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// ============================================================================
// Componente principal
// ============================================================================

export const ToolCallRenderer = memo(function ToolCallRenderer({
  tool,
  isStreaming,
}: ToolCallRendererProps) {
  const hint: RenderHint = tool.renderHint ?? "json";
  const renderer = RENDERERS[hint] ?? RENDERERS.json;

  return (
    <div
      className={`px-3 py-2 rounded-lg border text-xs ${tool.destructive ? "border-destructive/40 bg-destructive/5" : "border-border bg-muted/50"}`}
      data-testid="tool-call"
      data-tool-name={tool.name}
    >
      {/* Cabeçalho */}
      <div className="flex items-center gap-2 mb-1">
        <span
          className="font-semibold text-primary"
          data-testid="tool-call-name"
        >
          {tool.name}
        </span>
        {tool.destructive && (
          <span className="text-[10px] text-destructive/80 border border-destructive/40 rounded-sm px-1">
            destrutivo
          </span>
        )}
        {tool.category && tool.category !== "general" && (
          <span className="text-[10px] text-muted-foreground border border-border rounded-sm px-1">
            {tool.category}
          </span>
        )}
        {isStreaming && !tool.output && (
          <span className="ml-auto flex items-center gap-1 text-[10px] text-primary/70 font-medium">
            <Loader2 className="w-2.5 h-2.5 animate-spin" />
            executando…
          </span>
        )}
        {tool.elapsedMs !== undefined && (
          <span
            data-testid="tool-elapsed"
            className="ml-auto text-[10px] text-muted-foreground tabular-nums"
          >
            {formatElapsed(tool.elapsedMs)}
          </span>
        )}
      </div>

      {/* Argumentos */}
      <JsonViewer data={tool.args} label="Ver argumentos" />

      {/* Output ao vivo do terminal — chega linha a linha enquanto o comando
          ainda roda (evento `terminal_line`), antes do tool_result final. */}
      {tool.output == null &&
        tool.renderHint === "terminal_output" &&
        (tool.liveOutputLines?.length ?? 0) > 0 && (
          <TerminalBlock content={tool.liveOutputLines!.join("\n")} />
        )}

      {/* M4 — Tool result pendente: pulse skeleton enquanto aguarda resposta */}
      {tool.output == null &&
        (isStreaming ?? true) &&
        !(
          tool.renderHint === "terminal_output" && tool.liveOutputLines?.length
        ) && (
          <div className="mt-1.5 border-t border-border/40 pt-1.5 space-y-1.5">
            <div className="h-2.5 w-3/4 rounded-full bg-muted/70 animate-pulse" />
            <div className="h-2.5 w-1/2 rounded-full bg-muted/50 animate-pulse" />
          </div>
        )}

      {/* Output — renderizado pelo renderer do hint */}
      {tool.output != null && (
        <div className="mt-1.5 border-t border-border/40 pt-1.5">
          {renderer(tool.output, tool.name)}
        </div>
      )}
    </div>
  );
});
