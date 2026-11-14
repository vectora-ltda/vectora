// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { useLicenseQuery, type LicenseStatus } from "../license";
import {
  useActiveWorkspace,
  useInvalidateWorkspaces,
  useWorkspacesQuery,
  workspacesQueryKey,
} from "../workspaces";
import { useLicenseStatus } from "@/lib/hooks/use-license-status";

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  };
}

const client = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });

const license = (over: Partial<LicenseStatus> = {}): LicenseStatus => ({
  configured: true,
  tier: "pro",
  status: "active",
  days_remaining: 20,
  expires_at: "2026-12-01",
  cached: false,
  ...over,
});

const respond = (body: unknown, status = 200) =>
  Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response);

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("licença", () => {
  it("devolve o status do servidor, com as credenciais da sessão", async () => {
    fetchMock.mockReturnValue(respond(license()));

    const { result } = renderHook(() => useLicenseQuery(), {
      wrapper: wrapper(client()),
    });

    await waitFor(() => expect(result.current.data?.tier).toBe("pro"));
    expect(fetchMock).toHaveBeenCalledWith(
      "/license/status",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("503 (servidor de licença fora) ainda entrega o corpo, como offline", async () => {
    fetchMock.mockReturnValue(respond(license({ status: "offline" }), 503));

    const { result } = renderHook(() => useLicenseQuery(), {
      wrapper: wrapper(client()),
    });

    await waitFor(() => expect(result.current.data?.status).toBe("offline"));
  });

  it("erro do servidor vira erro da query, sem dados", async () => {
    fetchMock.mockReturnValue(respond({}, 500));

    const { result } = renderHook(() => useLicenseQuery(), {
      wrapper: wrapper(client()),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });

  describe("useLicenseStatus (formato antigo sobre a query)", () => {
    it("começa carregando e entrega o status", async () => {
      fetchMock.mockReturnValue(respond(license({ tier: "plus" })));

      const { result } = renderHook(() => useLicenseStatus(), {
        wrapper: wrapper(client()),
      });

      expect(result.current.loading).toBe(true);
      expect(result.current.status).toBeNull();
      await waitFor(() => expect(result.current.status?.tier).toBe("plus"));
      expect(result.current.loading).toBe(false);
    });

    it("falha mantém status nulo em vez de lançar", async () => {
      fetchMock.mockReturnValue(respond({}, 500));

      const { result } = renderHook(() => useLicenseStatus(), {
        wrapper: wrapper(client()),
      });

      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.status).toBeNull();
    });

    it("refetch busca de novo", async () => {
      fetchMock.mockReturnValue(respond(license()));
      const { result } = renderHook(() => useLicenseStatus(), {
        wrapper: wrapper(client()),
      });
      await waitFor(() => expect(result.current.status).not.toBeNull());
      fetchMock.mockReturnValue(respond(license({ status: "expired" })));

      await result.current.refetch();

      await waitFor(() =>
        expect(result.current.status?.status).toBe("expired"),
      );
    });
  });
});

const workspaces = [
  { id: "a", name: "A", cwd: "/a" },
  { id: "b", name: "B", cwd: "/b" },
];

describe("workspaces", () => {
  it("lista os workspaces do servidor", async () => {
    fetchMock.mockReturnValue(respond({ workspaces, active_id: "b" }));

    const { result } = renderHook(() => useWorkspacesQuery(), {
      wrapper: wrapper(client()),
    });

    await waitFor(() =>
      expect(result.current.data?.workspaces).toHaveLength(2),
    );
    expect(fetchMock).toHaveBeenCalledWith("/workspaces", {
      credentials: "include",
    });
  });

  it("o ativo é o marcado; sem marca, o primeiro; sem nenhum, nulo", async () => {
    fetchMock.mockReturnValue(respond({ workspaces, active_id: "b" }));
    const marcado = renderHook(() => useActiveWorkspace(), {
      wrapper: wrapper(client()),
    });
    await waitFor(() => expect(marcado.result.current?.id).toBe("b"));

    fetchMock.mockReturnValue(respond({ workspaces, active_id: null }));
    const primeiro = renderHook(() => useActiveWorkspace(), {
      wrapper: wrapper(client()),
    });
    await waitFor(() => expect(primeiro.result.current?.id).toBe("a"));

    fetchMock.mockReturnValue(respond({ workspaces: [], active_id: null }));
    const nenhum = renderHook(() => useActiveWorkspace(), {
      wrapper: wrapper(client()),
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(nenhum.result.current).toBeNull();
  });

  it("marcado que não existe mais cai no primeiro", async () => {
    fetchMock.mockReturnValue(respond({ workspaces, active_id: "sumiu" }));

    const { result } = renderHook(() => useActiveWorkspace(), {
      wrapper: wrapper(client()),
    });

    await waitFor(() => expect(result.current?.id).toBe("a"));
  });

  it("falha ao listar vira erro da query", async () => {
    fetchMock.mockReturnValue(respond({}, 500));

    const { result } = renderHook(() => useWorkspacesQuery(), {
      wrapper: wrapper(client()),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it("invalidar refaz a busca", async () => {
    fetchMock.mockReturnValue(respond({ workspaces, active_id: "a" }));
    const qc = client();
    const { result } = renderHook(
      () => ({
        q: useWorkspacesQuery(),
        invalidate: useInvalidateWorkspaces(),
      }),
      { wrapper: wrapper(qc) },
    );
    await waitFor(() => expect(result.current.q.data).toBeDefined());
    fetchMock.mockReturnValue(
      respond({ workspaces: [workspaces[0]], active_id: "a" }),
    );

    await result.current.invalidate();

    await waitFor(() =>
      expect(result.current.q.data?.workspaces).toHaveLength(1),
    );
    expect(qc.getQueryState(workspacesQueryKey)?.isInvalidated).toBe(false);
  });
});
