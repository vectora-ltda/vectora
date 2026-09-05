/**
 * Cliente HTTP do frontend para o backend FastAPI.
 *
 * - Streaming via SSE (`streamChat`, `resumeChat`) consome `Response.body`
 *   diretamente; abortar requer passar `AbortSignal`.
 * - Em 401 retenta uma vez via `/auth/refresh` antes de redirecionar para
 *   `/auth/signin`. Refresh concorrente não é coordenado.
 * - Caminhos relativos no mesmo origin do backend; cookies httpOnly viajam
 *   em todas as chamadas via `credentials: "include"`.
 *
 * Schemas espelham `src/api/schemas.py` (Pydantic).
 */

import { VECTORA_API_URL } from "@/lib/constants/api";
import { saveReturnTo } from "@/lib/utils/return-to";

// ============================================================================
// Types — espelham os schemas do src/api/schemas.py
// ============================================================================

export interface ChatConfig {
  model?: string;
  llm_provider?: string;
  recursion_limit?: number;
  workspace_id?: string;
  /** Sinal explícito de "criar workspace dedicado pra essa conversa" —
   * distinto de workspace_id ausente (que significa "reusa o ativo"). */
  create_new_workspace?: boolean;
  /** Modo Chat: conversacional puro, sem workspace/tools de dev. */
  chat_mode?: boolean;
  /** L4 — instrução personalizada prefixada ao system prompt */
  custom_system_prompt?: string;
  /** R2 — ask|accept_edits|plan|auto|bypass */
  permission_mode?: string;
  /** R4 — low|medium|high|max (vazio = default do modelo) */
  reasoning_effort?: string;
  /** Fork de checkpoint (editar mensagem / regenerar resposta) — checkpoint_id
   * pai da mensagem alvo (ver HistoryMessage.checkpoint_id). Resumir a
   * partir dele faz o histórico ramificar dali; o histórico original
   * continua intacto, só deixa de ser o branch "atual" da thread. */
  fork_from_checkpoint_id?: string;
}

/** Tipo semântico do attachment — espelha AttachmentKind do backend. */
export type AttachmentKind = "image" | "pdf" | "code" | "text" | "audio";

/**
 * Arquivo anexado a uma mensagem.
 * ``base64_data`` é o conteúdo puro em base64, sem prefixo data URL.
 */
export interface Attachment {
  kind: AttachmentKind;
  name: string;
  mime_type: string;
  base64_data: string;
}

export interface StreamChatRequest {
  thread_id?: string;
  content: string;
  config?: ChatConfig;
  /** Arquivos anexados à mensagem (F1 — multimodal). */
  attachments?: Attachment[];
}

export interface ResumeChatRequest {
  thread_id: string;
  interrupt_id: string;
  decision: "approve" | "reject" | `edit:${string}`;
}

/** Evento discriminado pelo campo `type` */
export type StreamEvent =
  | { type: "thread"; thread_id: string; workspace_id?: string }
  | { type: "token"; content: string; node?: string }
  | {
      type: "tool_call";
      tool_name: string;
      tool_call_id: string;
      args_json: string;
      render_hint?: string;
      category?: string;
      destructive?: boolean;
      icon?: string;
    }
  | {
      type: "tool_result";
      tool_call_id: string;
      content_json: string;
      is_error?: boolean;
    }
  | {
      type: "subagent_output";
      subagent_type: string;
      description?: string;
      status: "running" | "complete" | "error" | "cancelled";
      tool_call_id: string;
      content: string;
      is_delta?: boolean;
    }
  | {
      type: "node";
      node: string;
      status: "started" | "finished";
      duration_ms?: number;
      node_label?: string;
    }
  | {
      type: "ui_metrics";
      last_node?: string;
      last_node_ms?: number;
      rag_hits?: number;
      rag_misses?: number;
      tool_calls?: Record<string, number>;
    }
  | {
      type: "hitl";
      tool_name: string;
      args_json: string;
      interrupt_id: string;
      /** Razão pela qual o modelo pediu aprovação (opcional). */
      reasoning?: string;
      /** Preview do diff antes/depois, formato unified (opcional). */
      diff_preview?: string;
      /** Caminhos de arquivo afetados (opcional). */
      affected_paths?: string[];
      /** Modo de permissão ativo (default/yolo/etc.) (opcional). */
      permission_mode?: string;
      /** Anotação da aprovação inteligente — nunca decide sozinha. */
      pre_approved?: boolean;
    }
  | {
      type: "rag_citations";
      citations: Array<{ index: number; source: string; chunk: string }>;
    }
  | { type: "error"; message: string; code?: string }
  | { type: "done"; thread_id: string; run_id?: string }
  | { type: "message_break" }
  | { type: "workbench_invalidate"; tabs: string[]; tool_name?: string }
  | {
      type: "tool_activity";
      tool_name: string;
      tool_call_id: string;
      args_preview: string;
      elapsed_ms: number | null;
    }
  | { type: "model_switched"; from_model: string; to_model: string }
  | { type: "terminal_line"; line: string }
  | { type: "todos_updated"; todos: TodoItem[] };

/** Item da checklist de write_todos (TodoListMiddleware) — Plan Mode real. */
export interface TodoItem {
  content: string;
  status: "pending" | "in_progress" | "completed";
}

export interface Thread {
  id: string;
  created_at: string;
  updated_at: string;
  title?: string;
  /** Workspace associado à sessão (R — workspace por sessão). */
  workspace_id?: string;
  /** Modo da sessão: "chat" | "dev". Sessões legadas sem modo são "dev". */
  mode?: string;
  /** Sessão fixada — aparece no topo da lista da sidebar. */
  pinned?: boolean;
}

/** Anexo persistido de uma mensagem do histórico — `url`, quando presente,
 * aponta pra `GET /threads/{id}/attachments/{filename}` (sobrevive a
 * restart do backend, diferente do base64 que só existe durante o turno
 * ao vivo). */
export interface HistoryAttachment {
  name: string;
  mimeType: string;
  kind: string;
  size: number;
  url?: string | null;
}

export interface HistoryMessage {
  role: "human" | "assistant";
  content: string;
  created_at?: string;
  /** Checkpoint pai (estado do thread imediatamente antes desta mensagem
   * existir) — alvo de fork pra "editar e reenviar"/"regenerar". Vazio
   * quando o backend não conseguiu resolver. */
  checkpoint_id?: string;
  attachments?: HistoryAttachment[];
}

export interface ToolSchema {
  name: string;
  description: string;
  render_hint: string;
  category: string;
  destructive: boolean;
  icon: string;
  args_schema_json: string;
}

export interface GetToolsResponse {
  tools: ToolSchema[];
}

// ============================================================================
// Token refresh automático
// ============================================================================

/**
 * Tenta renovar o access token via /auth/refresh.
 *
 * Chamado automaticamente quando qualquer endpoint retorna 401.
 * Em produção (binário Nuitka) e em dev (Vite proxy), o browser fala
 * direto com o FastAPI — cookies httpOnly viajam no `credentials: include`.
 *
 * @returns true se o refresh foi bem-sucedido
 */
async function tryRefreshToken(): Promise<boolean> {
  try {
    const res = await fetch("/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({}),
    });
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Redireciona para login quando o refresh falha.
 * No-op em SSR (sem window).
 */
function redirectToLogin(): void {
  if (typeof window !== "undefined") {
    // Guarda onde o usuário estava antes do hard-redirect (que descarta a
    // navegação em memória); `signin.tsx` consome e volta pra cá.
    saveReturnTo(window.location.pathname + window.location.search);
    window.location.href = "/auth/signin";
  }
}

// ============================================================================
// Chat streaming
// ============================================================================

/**
 * Inicia ou continua um chat via SSE streaming.
 *
 * Se o backend retornar 401 (token expirado), renova o access token
 * automaticamente via /auth/refresh e retenta uma vez antes de
 * redirecionar para /auth/signin.
 *
 * @yields StreamEvent — eventos tipados (token, tool_call, done, etc.)
 */
export async function* streamChat(
  request: StreamChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const url = `${VECTORA_API_URL}/vectora.chat.v1.ChatService/StreamChat`;

  const doFetch = () =>
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(request),
      signal,
    });

  let response = await doFetch();

  // Refresh automático quando o access token expira (TTL: 15 min)
  if (response.status === 401) {
    const refreshed = await tryRefreshToken();
    if (!refreshed) {
      redirectToLogin();
      throw new Error("StreamChat: sessão expirada. Faça login novamente.");
    }
    response = await doFetch();
  }

  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => "");
    throw new Error(`StreamChat failed (${response.status}): ${text}`);
  }

  yield* readSSEStream(response.body);
}

/**
 * Retoma uma execução pausada (HITL).
 */
export async function* resumeChat(
  request: ResumeChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const url = `${VECTORA_API_URL}/vectora.chat.v1.ChatService/ResumeChat`;

  const doFetch = () =>
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(request),
      signal,
    });

  let response = await doFetch();

  if (response.status === 401) {
    const refreshed = await tryRefreshToken();
    if (!refreshed) {
      redirectToLogin();
      throw new Error("ResumeChat: sessão expirada. Faça login novamente.");
    }
    response = await doFetch();
  }

  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => "");
    throw new Error(`ResumeChat failed (${response.status}): ${text}`);
  }

  yield* readSSEStream(response.body);
}

// ============================================================================
// Thread management
// ============================================================================

async function postRpc<T>(
  path: string,
  body: object,
  isRetry = false,
): Promise<T> {
  const response = await fetch(`${VECTORA_API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (response.status === 401 && !isRetry) {
    const refreshed = await tryRefreshToken();
    if (refreshed) return postRpc(path, body, true);
    redirectToLogin();
    throw new Error(`${path}: sessão expirada. Faça login novamente.`);
  }
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${path} failed (${response.status}): ${text}`);
  }
  return response.json();
}

export const createThread = (workspaceId?: string | null): Promise<Thread> =>
  postRpc("/vectora.chat.v1.ThreadService/CreateThread", {
    workspace_id: workspaceId ?? "",
  });

/** Transcreve um dictado de voz gravado no cliente (MediaRecorder) — fallback
 * usado quando a Web Speech API do browser não está disponível (desktop). */
export const transcribeAudio = (
  audioBase64: string,
  mimeType: string,
  filename = "recording.webm",
): Promise<{ text: string }> =>
  postRpc("/vectora.chat.v1.ChatService/TranscribeAudio", {
    audio_base64: audioBase64,
    mime_type: mimeType,
    filename,
  });

export const getThread = (thread_id: string): Promise<Thread> =>
  postRpc("/vectora.chat.v1.ThreadService/GetThread", { thread_id });

export const listThreads = (limit = 50): Promise<{ threads: Thread[] }> =>
  postRpc("/vectora.chat.v1.ThreadService/ListThreads", { limit });

export const deleteThread = (thread_id: string): Promise<{}> =>
  postRpc("/vectora.chat.v1.ThreadService/DeleteThread", { thread_id });

export const updateThread = (
  thread_id: string,
  updates: { title?: string; pinned?: boolean },
): Promise<Thread> =>
  postRpc("/vectora.chat.v1.ThreadService/UpdateThread", {
    thread_id,
    ...(updates.title !== undefined ? { title: updates.title } : {}),
    ...(updates.pinned !== undefined ? { pinned: updates.pinned } : {}),
  });

export const getHistory = (
  thread_id: string,
): Promise<{ messages: HistoryMessage[]; todos?: TodoItem[] }> => {
  // Sessão ainda sem id (rota /session/new antes do primeiro turno, efeito
  // disparando durante a hidratação): pedir histórico de "" só rende um 404
  // com traceback no backend. Thread sem id é thread sem histórico.
  if (!thread_id.trim()) return Promise.resolve({ messages: [] });
  return postRpc("/vectora.chat.v1.ThreadService/GetHistory", { thread_id });
};

export interface PagedHistoryResponse {
  messages: HistoryMessage[];
  has_more: boolean;
  total_count: number;
}

/** Carrega mensagens antigas paginadas (GC + scroll infinito). */
export const getHistoryPage = (
  thread_id: string,
  limit = 200,
  offset = 0,
): Promise<PagedHistoryResponse> =>
  fetch(
    `/threads/${encodeURIComponent(thread_id)}/history?limit=${limit}&offset=${offset}`,
  ).then((r) => r.json() as Promise<PagedHistoryResponse>);

/** Pede ao backend um título gerado pela IA (idempotente: só no 1º turno). */
export const generateTitle = (thread_id: string): Promise<{ title: string }> =>
  postRpc("/vectora.chat.v1.ThreadService/GenerateTitle", { thread_id });

export interface ThreadPins {
  thread_id: string;
  pins: string[];
}

/** Lê os arquivos fixados da sessão (fonte de verdade: backend). */
export const getThreadPins = (thread_id: string): Promise<ThreadPins> =>
  postRpc("/vectora.chat.v1.ThreadService/GetThreadPins", { thread_id });

/** Grava os arquivos fixados da sessão; devolve a lista normalizada. */
export const setThreadPins = (
  thread_id: string,
  pins: string[],
): Promise<ThreadPins> =>
  postRpc("/vectora.chat.v1.ThreadService/SetThreadPins", { thread_id, pins });

// ============================================================================
// Auth usage — quota consumption
// ============================================================================

export interface UsageWindowStats {
  used: number;
  limit: number;
  remaining: number;
  window_seconds: number;
  reset_in_seconds: number;
}

export interface AuthUsage extends UsageWindowStats {
  five_hour: UsageWindowStats;
  weekly: UsageWindowStats;
}

export async function getAuthUsage(): Promise<AuthUsage> {
  const res = await fetch("/auth/usage", {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<AuthUsage>;
}

// ============================================================================
// Thread activity — files touched + turn count
// ============================================================================

export interface ThreadActivity {
  files_touched: string[];
  tool_call_counts: Record<string, number>;
  turn_count: number;
}

export async function getThreadActivity(
  threadId: string,
): Promise<ThreadActivity> {
  const res = await fetch(`/threads/${encodeURIComponent(threadId)}/activity`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok)
    return { files_touched: [], tool_call_counts: {}, turn_count: 0 };
  return res.json() as Promise<ThreadActivity>;
}

// ============================================================================
// Stack hint — detects project type for contextual suggestions
// ============================================================================

export async function getStackHint(
  workspaceId: string,
): Promise<{ stack: string }> {
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/stack-hint`,
    { headers: { Accept: "application/json" } },
  );
  if (!res.ok) return { stack: "unknown" };
  return res.json() as Promise<{ stack: string }>;
}

// ============================================================================
// Share (read-only, rota pública — sem auth necessária)
// ============================================================================

export interface SharedThread {
  thread_id: string;
  title?: string;
  messages: HistoryMessage[];
  created_at: string;
  expires_at?: string;
}

export async function getSharedThread(
  token: string,
): Promise<SharedThread | null> {
  const res = await fetch(`${VECTORA_API_URL}/threads/share/${token}`, {
    headers: { Accept: "application/json" },
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`share/${token}: ${res.status}`);
  return res.json() as Promise<SharedThread>;
}

export async function getTools(): Promise<GetToolsResponse> {
  const url = `${VECTORA_API_URL}/vectora.chat.v1.ChatService/GetTools`;
  let res = await fetch(url, { credentials: "include" });
  if (res.status === 401) {
    const refreshed = await tryRefreshToken();
    if (!refreshed) {
      redirectToLogin();
      throw new Error("unauthorized");
    }
    res = await fetch(url, { credentials: "include" });
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ============================================================================
// SSE parser interno
// ============================================================================

/**
 * Parseia uma única linha SSE (`data: {...}`) e retorna o evento tipado, ou
 * `null` para linhas irrelevantes/vazias (`[DONE]`, whitespace, não-`data:`).
 * Compartilhada entre o parse incremental (linhas completas) e o flush do
 * buffer residual quando o stream fecha.
 */
function parseSSELine(line: string): StreamEvent | null {
  if (!line.startsWith("data: ")) return null;
  const json = line.slice(6).trim();
  if (!json || json === "[DONE]") return null;

  try {
    return JSON.parse(json) as StreamEvent;
  } catch {
    // Linha malformada — ignorar
    console.warn("[vectora-client] SSE parse error:", json);
    return null;
  }
}

async function* readSSEStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<StreamEvent> {
  const decoder = new TextDecoder();
  const reader = body.getReader();
  let buffer = "";

  try {
    while (true) {
      // SSE é stream sequencial — Promise.all() seria incorreto aqui.
      // eslint-disable-next-line no-await-in-loop
      const { done, value } = await reader.read();
      if (done) {
        // O socket pode fechar exatamente no meio de uma linha `data:
        // {...}` que nunca teve seu `\n\n` terminador entregue — sem este
        // flush, esse último evento (às vezes o próprio `done`/`error` do
        // backend) seria descartado silenciosamente, sem erro nem warning.
        if (buffer.trim()) {
          const event = parseSSELine(buffer);
          if (event) yield event;
        }
        break;
      }

      buffer += decoder.decode(value, { stream: true });

      // Processar linhas completas
      const lines = buffer.split("\n");
      // A última linha pode estar incompleta — guardar no buffer
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const event = parseSSELine(line);
        if (!event) continue;
        yield event;
        if (event.type === "done" || event.type === "error") return;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
