import type { Thread as VectoraThread } from "@/lib/api/vectora-client";

/**
 * Constrói a entrada de thread inserida otimisticamente no cache do React
 * Query antes do backend confirmar a criação. `mode` precisa vir explícito
 * — `Thread.mode` é opcional e a sidebar (`sidebar.tsx`) trata ausência de
 * `mode` como sessão legada de código (`(t.mode ?? "code")`), então uma
 * conversa nova sem esse campo aparece do lado errado até o próximo
 * `ListThreads` completo.
 */
export function buildOptimisticThread(params: {
  id: string;
  title: string;
  workspaceId: string;
  chatMode: boolean;
  now?: string;
}): VectoraThread {
  const now = params.now ?? new Date().toISOString();
  return {
    id: params.id,
    created_at: now,
    updated_at: now,
    title: params.title,
    workspace_id: params.workspaceId,
    mode: params.chatMode ? "chat" : "code",
  };
}
