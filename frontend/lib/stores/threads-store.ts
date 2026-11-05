/**
 * Threads Store — Zustand
 *
 * Cache client-side de mensagens por thread. Resolve o flash vazio que
 * acontecia ao trocar de conversa:
 *
 *   Antes:  click thread → messages = [] → fetch (~500ms) → render
 *   Depois: click thread → render cache instantâneo → revalidate em background
 *
 * Stale-while-revalidate: sempre exibe o cache se existir, e dispara um
 * fetch silencioso para atualizar. O componente nunca vê estado vazio se
 * a thread já foi visitada nesta sessão.
 *
 * Não persiste em localStorage — cache é por sessão de browser (intencional:
 * o backend é a source of truth, queremos invalidar ao recarregar a página).
 *
 * GC automático: no máximo MESSAGES_IN_MEMORY_CAP entradas no cache;
 * TTL de 5min (por inatividade); pressão total estimada ≤ 50MB.
 * Entradas eviccionadas podem ser recuperadas via GET /threads/{id}/history.
 */

import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { Message } from "@/lib/types";

/** Limite de entradas simultâneas no cache (threads únicas). */
export const MESSAGES_IN_MEMORY_CAP = 200;
/** Inatividade máxima antes de GC (ms). */
const CACHE_TTL_MS = 5 * 60 * 1000;
/** Pressão máxima estimada do cache (bytes). */
const CACHE_MAX_BYTES = 50 * 1024 * 1024; // 50 MiB

interface ThreadCacheEntry {
  messages: Message[];
  /** Quando o cache foi populado a partir do backend. */
  fetchedAt: number;
  /** Quando o usuário interagiu pela última vez (envio/recebimento). */
  updatedAt: number;
  /** Tamanho estimado em bytes (JSON do array de mensagens). */
  sizeBytes: number;
}

/** Estima o tamanho em bytes de um array de mensagens. */
function estimateBytes(messages: Message[]): number {
  try {
    // Otimização: se houver mensagens com base64 muito longo, o JSON.stringify
    // pode travar a main thread. Usamos uma estimativa baseada no comprimento
    // das strings se o array for muito grande ou contiver campos suspeitos.
    let total = 0;
    for (const msg of messages) {
      total += (msg.content?.length || 0) * 2; // 2 bytes por char (UTF-16)
      if (msg.images) {
        for (const img of msg.images) {
          total += img.base64?.length || 0;
          total += img.url?.length || 0;
        }
      }
      // Outros campos (metadados, tool calls)
      total += 512;
    }
    return total;
  } catch {
    return messages.length * 1024; // fallback conservador
  }
}

/**
 * Retorna um cache saneado: remove entradas expiradas (TTL) e então aplica
 * LRU por updatedAt até respeitar cap e limite de memória.
 */
function applyGC(
  cache: Record<string, ThreadCacheEntry>,
): Record<string, ThreadCacheEntry> {
  const now = Date.now();

  // 1. Remover entradas expiradas por TTL.
  let entries = Object.entries(cache).filter(
    ([, entry]) => now - entry.updatedAt < CACHE_TTL_MS,
  );

  // 2. Ordenar por updatedAt decrescente (mais recentes primeiro).
  entries.sort(([, a], [, b]) => b.updatedAt - a.updatedAt);

  // 3. Aplicar cap de quantidade.
  if (entries.length > MESSAGES_IN_MEMORY_CAP) {
    entries = entries.slice(0, MESSAGES_IN_MEMORY_CAP);
  }

  // 4. Aplicar pressão de memória (descarta os mais antigos até caber).
  let totalBytes = entries.reduce((sum, [, e]) => sum + e.sizeBytes, 0);
  while (totalBytes > CACHE_MAX_BYTES && entries.length > 1) {
    const removed = entries.pop()!;
    totalBytes -= removed[1].sizeBytes;
  }

  return Object.fromEntries(entries);
}

interface ThreadsState {
  /** Cache de mensagens por threadId. */
  cache: Record<string, ThreadCacheEntry>;
  /** Threads cujo refetch em background está em andamento (para deduplicação). */
  revalidating: Record<string, boolean>;

  // ── Reads ────────────────────────────────────────────────────────────────
  /** Retorna o cache da thread (ou undefined se nunca foi vista). */
  getCached: (threadId: string) => ThreadCacheEntry | undefined;

  // ── Writes ───────────────────────────────────────────────────────────────
  /** Sobrescreve o cache de uma thread (após fetch do backend). */
  setMessages: (threadId: string, messages: Message[]) => void;

  /** Substitui as mensagens via updater function. Use em stream updates. */
  patchMessages: (
    threadId: string,
    updater: (current: Message[]) => Message[],
  ) => void;

  /** Marca/desmarca revalidação em andamento. */
  setRevalidating: (threadId: string, value: boolean) => void;

  /** Remove uma thread do cache (após delete). */
  invalidate: (threadId: string) => void;

  /** Limpa todo o cache (logout, troca de usuário). */
  clear: () => void;

  /**
   * Executa GC manualmente (TTL + cap + pressão de memória).
   * Chamado internamente a cada escrita; pode ser chamado externamente
   * por um setInterval periódico se necessário.
   */
  gcCache: () => void;
}

export const useThreadsStore = create<ThreadsState>()(
  immer((set, get) => ({
    cache: {},
    revalidating: {},

    getCached: (threadId) => get().cache[threadId],

    setMessages: (threadId, messages) => {
      const now = Date.now();
      const sizeBytes = estimateBytes(messages);
      set((state) => {
        const updatedCache = applyGC({
          ...state.cache,
          [threadId]: { messages, fetchedAt: now, updatedAt: now, sizeBytes },
        });
        return { cache: updatedCache };
      });
    },

    patchMessages: (threadId, updater) => {
      set((state) => {
        const prev = state.cache[threadId];
        const prevMessages = prev?.messages ?? [];
        const next = updater(prevMessages);
        // Identidade preservada se updater retornar o mesmo array.
        if (next === prevMessages && prev) return state;
        const now = Date.now();
        const sizeBytes = estimateBytes(next);
        const updatedCache = applyGC({
          ...state.cache,
          [threadId]: {
            messages: next,
            fetchedAt: prev?.fetchedAt ?? now,
            updatedAt: now,
            sizeBytes,
          },
        });
        return { cache: updatedCache };
      });
    },

    setRevalidating: (threadId, value) => {
      set((state) => {
        if (state.revalidating[threadId] === value) return state;
        const nextRevalidating = { ...state.revalidating };
        if (value) nextRevalidating[threadId] = true;
        else delete nextRevalidating[threadId];
        return { revalidating: nextRevalidating };
      });
    },

    invalidate: (threadId) => {
      set((state) => {
        if (!(threadId in state.cache) && !(threadId in state.revalidating))
          return state;
        const nextCache = { ...state.cache };
        const nextRevalidating = { ...state.revalidating };
        delete nextCache[threadId];
        delete nextRevalidating[threadId];
        return { cache: nextCache, revalidating: nextRevalidating };
      });
    },

    clear: () => set({ cache: {}, revalidating: {} }),

    gcCache: () => {
      set((state) => {
        const next = applyGC(state.cache);
        // Evita re-render desnecessário se nada foi removido.
        if (Object.keys(next).length === Object.keys(state.cache).length)
          return;
        state.cache = next;
      });
    },
  })),
);
