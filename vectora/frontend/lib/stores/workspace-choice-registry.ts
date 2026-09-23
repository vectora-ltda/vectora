import { useSyncExternalStore } from "react";

/**
 * Registro de threads cujo workspace já foi escolhido nesta navegação SPA.
 *
 * Uma sessão nova em modo Code precisa que o usuário escolha um workspace antes
 * de começar (em vez de herdar silenciosamente o workspace global persistido).
 * Quando o usuário confirma a escolha no modal de "Nova conversa", marcamos o id
 * aqui — assim a rota de sessão sabe que NÃO deve reabrir o seletor para esse id
 * (evita um loop, já que confirmar gera um id novo e marcado).
 *
 * TTL: entradas expiram após 5 minutos, espelhando o new-thread-registry, para
 * que um reload tardio não deixe a marcação presa para sempre.
 */

const TTL_MS = 5 * 60 * 1000;

const chosen = new Map<string, number>();
const listeners = new Map<string, Set<() => void>>();

function notify(threadId: string): void {
  listeners.get(threadId)?.forEach((listener) => listener());
}

export function subscribeWorkspaceChosen(
  threadId: string,
  listener: () => void,
): () => void {
  const bucket = listeners.get(threadId) ?? new Set<() => void>();
  bucket.add(listener);
  listeners.set(threadId, bucket);
  return () => {
    bucket.delete(listener);
    if (!bucket.size) listeners.delete(threadId);
  };
}

/** Marca que o workspace desta thread já foi escolhido pelo usuário. */
export function markWorkspaceChosen(threadId: string): void {
  chosen.set(threadId, Date.now());
  notify(threadId);
}

/** Retorna true se o usuário já escolheu o workspace desta thread. */
export function isWorkspaceChosen(threadId: string): boolean {
  const at = chosen.get(threadId);
  if (at === undefined) return false;
  if (Date.now() - at > TTL_MS) {
    chosen.delete(threadId);
    notify(threadId);
    return false;
  }
  return true;
}

/** Expõe a escolha de workspace de forma reativa para a UI. */
export function useIsWorkspaceChosen(threadId: string): boolean {
  return useSyncExternalStore(
    (listener) => subscribeWorkspaceChosen(threadId, listener),
    () => isWorkspaceChosen(threadId),
    () => false,
  );
}

/**
 * Sinal separado de `markWorkspaceChosen`: o usuário pediu explicitamente
 * "criar novo workspace para essa conversa" no modal (`onConfirm(null)`).
 * Sem isso, `use-stream-handler.ts` mandaria o `active_id` stale do
 * Zustand store (workspace de uma conversa anterior) como `workspace_id` da
 * request, e o backend nunca criaria o workspace dedicado pedido — ver
 * `ChatConfig.create_new_workspace` / `_resolve_workspace_id(force_new=...)`.
 */
const createNew = new Map<string, number>();

export function markCreateNewWorkspace(threadId: string): void {
  createNew.set(threadId, Date.now());
}

/** Consome o sinal (remove após ler) — vale só pro primeiro turno da
 * conversa; turnos seguintes já têm o workspace_id sincronizado de volta
 * (ver ThreadEvent.workspace_id + syncActiveLocal). */
export function consumeCreateNewWorkspace(threadId: string): boolean {
  const at = createNew.get(threadId);
  createNew.delete(threadId);
  if (at === undefined) return false;
  return Date.now() - at <= TTL_MS;
}
