import { useSyncExternalStore } from "react";

/**
 * Registro de threads "novas" — persiste entre navegações SPA sem reload.
 *
 * Quando createThread() gera um ID e redireciona para /session/<id>,
 * este módulo lembra que esse thread ainda não existe no backend
 * (ChatInterface usa isso para pular o fetch de histórico).
 *
 * TTL: entradas expiram automaticamente após 5 minutos. Isso garante que
 * um reload após crash não deixe a thread em estado "nova" para sempre.
 * clearNew() deve ser chamado explicitamente quando o thread é persistido
 * no backend (primeiro onThreadUpdate / first stream completion).
 */

const TTL_MS = 5 * 60 * 1000;

interface NewEntry {
  createdAt: number;
}

const newThreads = new Map<string, NewEntry>();
const listeners = new Map<string, Set<() => void>>();
const expiryTimers = new Map<string, ReturnType<typeof setTimeout>>();

function notify(threadId: string): void {
  listeners.get(threadId)?.forEach((listener) => listener());
}

/**
 * Inscreve um consumidor nas mudanças de um thread e devolve a função de cancelamento.
 * A notificação é limitada ao `threadId` informado.
 */
export function subscribeNewThread(
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

/** Marca um thread como recém-criado (não existe no backend ainda). */
export function markAsNew(threadId: string): void {
  const previousTimer = expiryTimers.get(threadId);
  if (previousTimer) clearTimeout(previousTimer);
  const createdAt = Date.now();
  newThreads.set(threadId, { createdAt });
  expiryTimers.set(
    threadId,
    setTimeout(() => {
      const entry = newThreads.get(threadId);
      if (entry?.createdAt !== createdAt) return;
      newThreads.delete(threadId);
      expiryTimers.delete(threadId);
      notify(threadId);
    }, TTL_MS + 1),
  );
  notify(threadId);
}

/** Retorna true se o thread foi criado localmente e ainda não persistido. */
export function isNew(threadId: string): boolean {
  const entry = newThreads.get(threadId);
  if (!entry) return false;
  return Date.now() - entry.createdAt <= TTL_MS;
}

/** Remove a marcação (chamado quando o thread é persistido no backend). */
export function clearNew(threadId: string): void {
  const timer = expiryTimers.get(threadId);
  if (timer) clearTimeout(timer);
  expiryTimers.delete(threadId);
  newThreads.delete(threadId);
  notify(threadId);
}

/** Expõe o estado de nova sessão de forma reativa para a UI. */
export function useIsNewThread(threadId: string): boolean {
  return useSyncExternalStore(
    (listener) => subscribeNewThread(threadId, listener),
    () => isNew(threadId),
    () => false,
  );
}
