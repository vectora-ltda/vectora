"use client";

/**
 * Hook das tarefas em segundo plano de uma session (rotina/heartbreak/webhook).
 *
 * CRUD + histórico de execuções, scoped por `threadId`. O backend é a fonte de
 * verdade (CLAUDE.md §8): toda mutação refaz o fetch. Cache SWR no
 * workbench-store (slice `tasks`) — reabrir a aba dentro do TTL não refaz o
 * fetch; fora do TTL ou após uma mutação, revalida. Atualizações ao vivo das
 * runs chegam via SSE de webhooks (`use-webhook-events`), tratadas pelo painel
 * (que chama `refetch()` ao receber um evento `background_run.*`).
 */

import { useCallback } from "react";
import { useWorkbenchSWR } from "@/lib/hooks/workbench/use-swr";
import {
  WORKBENCH_STALE_MS,
  useWorkbenchStore,
  type BackgroundTaskItem,
  type BackgroundRunItem,
} from "@/lib/stores/workbench-store";

export type BackgroundKind = BackgroundTaskItem["kind"];
export type TriggerType = BackgroundTaskItem["trigger_type"];
export type BackgroundTask = BackgroundTaskItem;
export type BackgroundRun = BackgroundRunItem;

export interface CreateTaskInput {
  kind: BackgroundKind;
  name: string;
  instruction: string;
  trigger_type: TriggerType;
  trigger_config?: Record<string, unknown>;
  workspace_id?: string | null;
}

function base(threadId: string): string {
  return `/sessions/${encodeURIComponent(threadId)}/background`;
}

async function asJson<T>(res: Response, fallback: T): Promise<T> {
  if (!res.ok) return fallback;
  try {
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

export function useBackgroundTasks(threadId: string): {
  tasks: BackgroundTask[];
  runs: BackgroundRun[];
  loading: boolean;
  refetch: () => Promise<void>;
  createTask: (input: CreateTaskInput) => Promise<boolean>;
  toggleTask: (id: string, enabled: boolean) => Promise<void>;
  deleteTask: (id: string) => Promise<void>;
  runTask: (id: string) => Promise<void>;
} {
  const cache = useWorkbenchStore((s) => s.getTasks(threadId));
  const setTasksData = useWorkbenchStore((s) => s.setTasksData);
  const invalidateTasks = useWorkbenchStore((s) => s.invalidateTasks);

  const fetchAndStore = useCallback(async () => {
    const [t, r] = await Promise.all([
      fetch(`${base(threadId)}/tasks`).then((res) =>
        asJson<BackgroundTask[]>(res, []),
      ),
      fetch(`${base(threadId)}/runs`).then((res) =>
        asJson<BackgroundRun[]>(res, []),
      ),
    ]);
    setTasksData(threadId, t, r);
  }, [threadId, setTasksData]);

  const fetchedAt = cache.fetchedAt;
  const isStale = useCallback(
    () => Date.now() - fetchedAt > WORKBENCH_STALE_MS,
    [fetchedAt],
  );

  useWorkbenchSWR({
    key: `tasks:${threadId}`,
    hasCache: cache.fetchedAt > 0,
    isStale,
    revalidate: fetchAndStore,
  });

  const refetch = useCallback(async () => {
    invalidateTasks(threadId);
    await fetchAndStore();
  }, [threadId, invalidateTasks, fetchAndStore]);

  const createTask = useCallback(
    async (input: CreateTaskInput): Promise<boolean> => {
      const res = await fetch(`${base(threadId)}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trigger_config: {}, ...input }),
      });
      if (res.ok) await refetch();
      return res.ok;
    },
    [threadId, refetch],
  );

  const toggleTask = useCallback(
    async (id: string, enabled: boolean): Promise<void> => {
      await fetch(`${base(threadId)}/tasks/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      await refetch();
    },
    [threadId, refetch],
  );

  const deleteTask = useCallback(
    async (id: string): Promise<void> => {
      await fetch(`${base(threadId)}/tasks/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      await refetch();
    },
    [threadId, refetch],
  );

  const runTask = useCallback(
    async (id: string): Promise<void> => {
      await fetch(`${base(threadId)}/tasks/${encodeURIComponent(id)}/run`, {
        method: "POST",
      });
      // A run é assíncrona; o painel revalida ao receber o SSE, mas refazemos
      // o fetch já para refletir o estado 'running'.
      await refetch();
    },
    [threadId, refetch],
  );

  return {
    tasks: cache.tasks,
    runs: cache.runs,
    loading: cache.fetchedAt === 0,
    refetch,
    createTask,
    toggleTask,
    deleteTask,
    runTask,
  };
}
