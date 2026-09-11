// @vitest-environment jsdom
/**
 * Tests para lib/queries/threads: useThreadsQuery (transform p/ Sidebar),
 * useDeleteThread (otimista + rollback) e useUpdateThread (invalida + broadcast).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { createElement, type ReactNode } from "react";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const listThreads = vi.fn();
const deleteThread = vi.fn();
const updateThread = vi.fn();
const markThreadRead = vi.fn();
const broadcastEvent = vi.fn();

vi.mock("@/lib/api/vectora-client", () => ({
  listThreads: (...a: unknown[]) => listThreads(...a),
  deleteThread: (...a: unknown[]) => deleteThread(...a),
  updateThread: (...a: unknown[]) => updateThread(...a),
  markThreadRead: (...a: unknown[]) => markThreadRead(...a),
}));

vi.mock("@/lib/hooks/use-broadcast-sync", () => ({
  broadcastEvent: (...a: unknown[]) => broadcastEvent(...a),
  BROADCAST_THREADS: "threads",
}));

import {
  useThreadsQuery,
  useDeleteThread,
  useUpdateThread,
  threadsQueryKey,
} from "@/lib/queries/threads";

function vthread(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    created_at: "2024-01-01",
    updated_at: "2024-01-02",
    title: "Título",
    workspace_id: "ws1",
    ...over,
  };
}

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
  return { qc, wrapper };
}

beforeEach(() => {
  listThreads.mockReset();
  deleteThread.mockReset();
  updateThread.mockReset();
  markThreadRead.mockReset();
  broadcastEvent.mockReset();
});

describe("threadsQueryKey", () => {
  it("inclui o limit — default THREAD_FETCH_LIMIT quando não passado", () => {
    expect(threadsQueryKey()).toEqual(["threads", 100]);
  });

  it("limit explícito diferente gera uma chave distinta", () => {
    expect(threadsQueryKey(50)).toEqual(["threads", 50]);
    expect(threadsQueryKey(50)).not.toEqual(threadsQueryKey());
  });
});

describe("useThreadsQuery", () => {
  it("desabilitada sem userId não chama listThreads", () => {
    const { wrapper } = makeWrapper();
    renderHook(() => useThreadsQuery(undefined), { wrapper });
    expect(listThreads).not.toHaveBeenCalled();
  });

  it("habilitada com userId carrega e transforma p/ formato do Sidebar", async () => {
    listThreads.mockResolvedValueOnce({ threads: [vthread("t1")] });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useThreadsQuery("u1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const t = result.current.data![0];
    expect(t.thread_id).toBe("t1");
    expect(t.metadata.user_id).toBe("u1");
    expect(t.metadata.title).toBe("Título");
    expect(t.workspace_id).toBe("ws1");
  });

  it("title null vira string vazia", async () => {
    listThreads.mockResolvedValueOnce({
      threads: [vthread("t1", { title: null })],
    });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useThreadsQuery("u1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data![0].metadata.title).toBe("");
  });

  it("propaga pinned da thread do backend", async () => {
    listThreads.mockResolvedValueOnce({
      threads: [vthread("t1", { pinned: true })],
    });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useThreadsQuery("u1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data![0].pinned).toBe(true);
  });

  it("propaga unread_count para a sidebar", async () => {
    listThreads.mockResolvedValueOnce({
      threads: [vthread("t1", { unread_count: 4 })],
    });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useThreadsQuery("u1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data![0].unread_count).toBe(4);
  });

  it("propaga atividade remota para a sidebar", async () => {
    listThreads.mockResolvedValueOnce({
      threads: [
        vthread("t1", {
          remote_activity: { last_active_at: "2026-09-10T12:00:00Z" },
        }),
      ],
    });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useThreadsQuery("u1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data![0].remote_activity).toEqual({
      last_active_at: "2026-09-10T12:00:00Z",
    });
  });

  it("erro/borda: pinned ausente no backend vira false", async () => {
    listThreads.mockResolvedValueOnce({ threads: [vthread("t1")] });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useThreadsQuery("u1"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data![0].pinned).toBe(false);
  });

  it("userId vazio mantém a query desabilitada", () => {
    const { wrapper } = makeWrapper();
    renderHook(() => useThreadsQuery(""), { wrapper });
    expect(listThreads).not.toHaveBeenCalled();
  });
});

describe("useDeleteThread", () => {
  it("mutateAsync chama deleteThread com o id", async () => {
    deleteThread.mockResolvedValue({});
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useDeleteThread(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("t1");
    });
    expect(deleteThread.mock.calls[0][0]).toBe("t1");
  });

  it("remove a thread otimisticamente do cache", async () => {
    const { qc, wrapper } = makeWrapper();
    qc.setQueryData(threadsQueryKey(), {
      threads: [vthread("t1"), vthread("t2")],
    });
    deleteThread.mockResolvedValue({});
    const { result } = renderHook(() => useDeleteThread(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("t1");
    });
    const data = qc.getQueryData<{ threads: { id: string }[] }>(
      threadsQueryKey(),
    );
    expect(data!.threads.map((t) => t.id)).toEqual(["t2"]);
  });

  it("faz rollback do cache quando deleteThread falha", async () => {
    const { qc, wrapper } = makeWrapper();
    qc.setQueryData(threadsQueryKey(), {
      threads: [vthread("t1"), vthread("t2")],
    });
    deleteThread.mockRejectedValue(new Error("falhou"));
    const { result } = renderHook(() => useDeleteThread(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("t1").catch(() => {});
    });
    const data = qc.getQueryData<{ threads: { id: string }[] }>(
      threadsQueryKey(),
    );
    expect(data!.threads.map((t) => t.id)).toEqual(["t1", "t2"]);
  });

  it("emite broadcast 'deleted' ao concluir", async () => {
    deleteThread.mockResolvedValue({});
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useDeleteThread(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("t9");
    });
    expect(broadcastEvent).toHaveBeenCalledWith("threads", {
      type: "deleted",
      id: "t9",
    });
  });
});

describe("useUpdateThread", () => {
  it("mutateAsync chama updateThread com id e updates", async () => {
    updateThread.mockResolvedValue(vthread("t1", { title: "Novo" }));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useUpdateThread(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({
        id: "t1",
        updates: { title: "Novo" },
      });
    });
    expect(updateThread).toHaveBeenCalledWith("t1", { title: "Novo" });
  });

  it("mutateAsync chama updateThread com id e pinned (fixar sem tocar no título)", async () => {
    updateThread.mockResolvedValue(vthread("t1", { pinned: true }));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useUpdateThread(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({
        id: "t1",
        updates: { pinned: true },
      });
    });
    expect(updateThread).toHaveBeenCalledWith("t1", { pinned: true });
  });

  it("emite broadcast 'renamed' com o novo título", async () => {
    updateThread.mockResolvedValue(vthread("t1", { title: "Renomeado" }));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useUpdateThread(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({
        id: "t1",
        updates: { title: "Renomeado" },
      });
    });
    expect(broadcastEvent).toHaveBeenCalledWith("threads", {
      type: "renamed",
      id: "t1",
      title: "Renomeado",
    });
  });

  it("invalida a query de threads no sucesso", async () => {
    updateThread.mockResolvedValue(vthread("t1"));
    const { qc, wrapper } = makeWrapper();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useUpdateThread(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ id: "t1", updates: { title: "x" } });
    });
    expect(spy).toHaveBeenCalledWith({ queryKey: threadsQueryKey() });
  });

  it("title null no retorno vira string vazia no broadcast", async () => {
    updateThread.mockResolvedValue(vthread("t1", { title: null }));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useUpdateThread(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ id: "t1", updates: {} });
    });
    expect(broadcastEvent).toHaveBeenCalledWith("threads", {
      type: "renamed",
      id: "t1",
      title: "",
    });
  });
});
