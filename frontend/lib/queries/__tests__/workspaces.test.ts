// @vitest-environment jsdom
/**
 * Tests para lib/queries/workspaces: useWorkspacesQuery (fetch /workspaces),
 * useActiveWorkspace (derivação do ativo) e useInvalidateWorkspaces.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { createElement, type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import {
  useWorkspacesQuery,
  useActiveWorkspace,
  useInvalidateWorkspaces,
  workspacesQueryKey,
} from "@/lib/queries/workspaces";
import type { WorkspaceInfo } from "@/lib/stores/workspaces-store";

function ws(id: string): WorkspaceInfo {
  return { id, name: id, path: `/${id}` } as unknown as WorkspaceInfo;
}

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
  return { qc, wrapper };
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

function okRes(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response;
}

describe("workspacesQueryKey", () => {
  it("é ['workspaces']", () => {
    expect(workspacesQueryKey).toEqual(["workspaces"]);
  });
});

describe("useWorkspacesQuery", () => {
  it("carrega a lista do backend", async () => {
    fetchMock.mockResolvedValueOnce(
      okRes({ workspaces: [ws("a"), ws("b")], active_id: "a" }),
    );
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useWorkspacesQuery(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.workspaces).toHaveLength(2);
    expect(result.current.data?.active_id).toBe("a");
  });

  it("erro HTTP vira isError", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
    } as unknown as Response);
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useWorkspacesQuery(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it("chama /workspaces com credentials include", async () => {
    fetchMock.mockResolvedValueOnce(okRes({ workspaces: [], active_id: null }));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useWorkspacesQuery(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/workspaces");
    expect(opts.credentials).toBe("include");
  });
});

describe("useActiveWorkspace", () => {
  it("retorna null sem dados em cache", () => {
    const { wrapper } = makeWrapper();
    fetchMock.mockResolvedValue(okRes({ workspaces: [], active_id: null }));
    const { result } = renderHook(() => useActiveWorkspace(), { wrapper });
    expect(result.current).toBeNull();
  });

  it("retorna o workspace de active_id", () => {
    const { qc, wrapper } = makeWrapper();
    qc.setQueryData(workspacesQueryKey, {
      workspaces: [ws("a"), ws("b")],
      active_id: "b",
    });
    const { result } = renderHook(() => useActiveWorkspace(), { wrapper });
    expect(result.current?.id).toBe("b");
  });

  it("retorna o primeiro quando active_id é null", () => {
    const { qc, wrapper } = makeWrapper();
    qc.setQueryData(workspacesQueryKey, {
      workspaces: [ws("a"), ws("b")],
      active_id: null,
    });
    const { result } = renderHook(() => useActiveWorkspace(), { wrapper });
    expect(result.current?.id).toBe("a");
  });

  it("faz fallback ao primeiro quando active_id não está na lista", () => {
    const { qc, wrapper } = makeWrapper();
    qc.setQueryData(workspacesQueryKey, {
      workspaces: [ws("a"), ws("b")],
      active_id: "inexistente",
    });
    const { result } = renderHook(() => useActiveWorkspace(), { wrapper });
    expect(result.current?.id).toBe("a");
  });

  it("retorna null com lista vazia", () => {
    const { qc, wrapper } = makeWrapper();
    qc.setQueryData(workspacesQueryKey, { workspaces: [], active_id: null });
    const { result } = renderHook(() => useActiveWorkspace(), { wrapper });
    expect(result.current).toBeNull();
  });

  it("retorna null com lista vazia mesmo com active_id setado", () => {
    const { qc, wrapper } = makeWrapper();
    qc.setQueryData(workspacesQueryKey, { workspaces: [], active_id: "x" });
    const { result } = renderHook(() => useActiveWorkspace(), { wrapper });
    expect(result.current).toBeNull();
  });
});

describe("useInvalidateWorkspaces", () => {
  it("retorna uma função que invalida a query key", async () => {
    const { qc, wrapper } = makeWrapper();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useInvalidateWorkspaces(), { wrapper });
    await result.current();
    expect(spy).toHaveBeenCalledWith({ queryKey: workspacesQueryKey });
  });
});
