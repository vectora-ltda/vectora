/**
 * TanStack Query hooks para threads.
 *
 * Substitui o padrão useState+useEffect em use-threads.ts. Cache compartilhado
 * via QueryClient: todas as instâncias de useThreadsQuery() lêem o mesmo dado
 * sem fazer fetches duplicados. Invalidação propagada automaticamente após
 * create/delete/update.
 */

import {
  useQuery,
  useMutation,
  useQueryClient,
  type UseQueryResult,
  type UseMutationResult,
} from "@tanstack/react-query";
import {
  listThreads,
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
      const prev = qc.getQueryData<{ threads: VectoraThread[] }>(
        threadsQueryKey(),
      );
      qc.setQueryData<{ threads: VectoraThread[] }>(
        threadsQueryKey(),
        (old) => ({
          threads: old?.threads.filter((t) => t.id !== threadId) ?? [],
        }),
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
