/**
 * TanStack Query hooks para threads.
 *
 * Substitui o padrão useState+useEffect em use-threads.ts. Cache compartilhado
 * via QueryClient: todas as instâncias de useThreadsQuery() lêem o mesmo dado
 * sem fazer fetches duplicados. Invalidação propagada automaticamente após
 * create/delete/update.
 */

import { useCallback, useRef, useState } from "react";
import {
  useQuery,
  useMutation,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
  type UseMutationResult,
} from "@tanstack/react-query";
import {
  listThreads,
  type ListThreadsResult,
  deleteThread,
  updateThread,
  type Thread as VectoraThread,
} from "@/lib/api/vectora-client";
import type { Thread } from "@/lib/hooks/threads";
import { THREAD_FETCH_LIMIT } from "@/lib/constants/features";
import {
  broadcastEvent,
  BROADCAST_THREADS,
} from "@/lib/hooks/use-broadcast-sync";

// Chave de cache parametrizada por `limit` — sem isso, dois consumidores
// que fazem `listThreads(limit)` com valores diferentes sob a MESMA chave
// colidem: um popula o cache com uma lista truncada, o outro lê essa lista
// stale dentro do `staleTime`. Todo consumidor de produção usa o default
// (THREAD_FETCH_LIMIT) — só testes/casos futuros passariam um limit
// explícito, e aí a chave diverge de propósito, não por acidente.
export function threadsQueryKey(
  limit: number = THREAD_FETCH_LIMIT,
): readonly ["threads", number] {
  return ["threads", limit] as const;
}

/**
 * Dado da query: as threads carregadas e, quando o servidor informa, o total do
 * filtro e o cursor da página seguinte.
 */
export type ThreadsCache = ListThreadsResult;

/**
 * Altera só a lista de threads do cache e preserva `total` e `next_cursor`.
 * Escrever `{ threads }` direto apagaria o cursor e esconderia o "carregar
 * mais" da sidebar.
 */
export function updateThreadsCache(
  qc: QueryClient,
  update: (threads: VectoraThread[]) => VectoraThread[],
): void {
  qc.setQueryData<ThreadsCache>(threadsQueryKey(), (old) => ({
    ...old,
    threads: update(old?.threads ?? []),
  }));
}

// ---------------------------------------------------------------------------
// Conversão VectoraThread → Thread (formato do Sidebar)
// ---------------------------------------------------------------------------

function toSidebarThread(t: VectoraThread, userId: string): Thread {
  return {
    thread_id: t.id,
    created_at: t.created_at,
    updated_at: t.updated_at,
    metadata: { user_id: userId, title: t.title ?? "" },
    workspace_id: t.workspace_id,
    // Modo da sessão (backend: "code" | "chat"); default "code" alinha ao
    // _normalize_mode. A sidebar separa os pools por este campo.
    mode: t.mode ?? "code",
    pinned: t.pinned ?? false,
    unread_count: t.unread_count ?? 0,
    remote_activity: t.remote_activity ?? null,
  };
}

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/** Lista de threads do usuário, convertida para o formato do Sidebar. */
export function useThreadsQuery(
  userId: string | undefined,
): UseQueryResult<Thread[], Error> {
  return useQuery({
    queryKey: threadsQueryKey(),
    queryFn: () => listThreads(THREAD_FETCH_LIMIT),
    select: (data) => data.threads.map((t) => toSidebarThread(t, userId ?? "")),
    enabled: !!userId,
    staleTime: 30_000,
    // A query só habilita com `userId` definido (enabled: !!userId); refetch em
    // todo mount evita servir um cache vazio até o staleTime expirar.
    refetchOnMount: "always",
  });
}

/** Paginação da lista: quantas threads faltam e como carregar a próxima página. */
export interface ThreadsPaging {
  hasMore: boolean;
  /** Threads do filtro ainda não carregadas. */
  remaining: number;
  isLoadingMore: boolean;
  loadMore: () => Promise<void>;
}

/**
 * Carrega a página seguinte da lista pelo cursor do servidor e a acrescenta ao
 * cache, sem repetir threads já carregadas (uma thread que mudou de lugar entre
 * as chamadas pode aparecer nas duas páginas).
 */
export function useThreadsPaging(userId: string | undefined): ThreadsPaging {
  const qc = useQueryClient();
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const loadingRef = useRef(false);
  const { data } = useQuery({
    queryKey: threadsQueryKey(),
    queryFn: () => listThreads(THREAD_FETCH_LIMIT),
    enabled: !!userId,
    staleTime: 30_000,
    select: (cache) => ({
      next: cache.next_cursor ?? "",
      remaining: Math.max(0, (cache.total ?? 0) - cache.threads.length),
    }),
  });

  const loadMore = useCallback(async () => {
    const current = qc.getQueryData<ThreadsCache>(threadsQueryKey());
    const cursor = current?.next_cursor;
    if (!cursor || loadingRef.current) return;
    loadingRef.current = true;
    setIsLoadingMore(true);
    try {
      const page = await listThreads(THREAD_FETCH_LIMIT, { cursor });
      qc.setQueryData<ThreadsCache>(threadsQueryKey(), (old) => {
        const known = new Set((old?.threads ?? []).map((t) => t.id));
        return {
          ...old,
          threads: [
            ...(old?.threads ?? []),
            ...page.threads.filter((t) => !known.has(t.id)),
          ],
          total: page.total ?? old?.total,
          next_cursor: page.next_cursor ?? "",
        };
      });
    } finally {
      loadingRef.current = false;
      setIsLoadingMore(false);
    }
  }, [qc]);

  return {
    hasMore: !!data?.next,
    remaining: data?.remaining ?? 0,
    isLoadingMore,
    loadMore,
  };
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/** Deleta uma thread com update otimista + rollback em erro. */
export function useDeleteThread(): UseMutationResult<
  Record<string, never>,
  Error,
  string
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteThread,
    onMutate: async (threadId) => {
      await qc.cancelQueries({ queryKey: threadsQueryKey() });
      const prev = qc.getQueryData<ThreadsCache>(threadsQueryKey());
      updateThreadsCache(qc, (threads) =>
        threads.filter((t) => t.id !== threadId),
      );
      return { prev };
    },
    onError: (_err, _id, ctx) => {
      if (ctx?.prev) qc.setQueryData(threadsQueryKey(), ctx.prev);
    },
    onSettled: (_data, _err, threadId) => {
      void qc.invalidateQueries({ queryKey: threadsQueryKey() });
      broadcastEvent(BROADCAST_THREADS, { type: "deleted", id: threadId });
    },
  });
}

/** Atualiza título e/ou pin da thread com invalidação. */
export function useUpdateThread(): UseMutationResult<
  VectoraThread,
  Error,
  { id: string; updates: { title?: string; pinned?: boolean } }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, updates }) => updateThread(id, updates),
    onSuccess: (thread) => {
      void qc.invalidateQueries({ queryKey: threadsQueryKey() });
      broadcastEvent(BROADCAST_THREADS, {
        type: "renamed",
        id: thread.id,
        title: thread.title ?? "",
      });
    },
  });
}
