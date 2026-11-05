// @vitest-environment jsdom
/**
 * Loaders de "/" e "/session/$threadId" — confirmam o fix da colisão de
 * cache: os dois populavam `threadsQueryKey` com `limit`
 * divergentes (1, 50, e useThreadsQuery usa THREAD_FETCH_LIMIT=100), o que
 * fazia `ensureQueryData` do loader visitado por último servir uma lista
 * truncada e stale dentro do `staleTime` de 30s. Também cobre o isolamento
 * de erro: falha em `getHistory` não pode bloquear a navegação quando a
 * lista de threads já carregou.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (opts: unknown) => opts,
}));

const listThreadsMock = vi.fn();
const getHistoryMock = vi.fn();

vi.mock("@/lib/api/vectora-client", () => ({
  listThreads: (...args: unknown[]) => listThreadsMock(...args),
  getHistory: (...args: unknown[]) => getHistoryMock(...args),
}));

const ensureQueryDataMock = vi.fn();
const prefetchQueryMock = vi.fn();

vi.mock("../../router", () => ({
  queryClient: {
    ensureQueryData: (...args: unknown[]) => ensureQueryDataMock(...args),
    prefetchQuery: (...args: unknown[]) => prefetchQueryMock(...args),
  },
}));

import { THREAD_FETCH_LIMIT } from "@/lib/constants/features";
import { Route as HomeRoute } from "../index";
import { Route as SessionRoute } from "../session/$threadId";

type LoaderOpts = { loader: (args?: unknown) => unknown };

beforeEach(() => {
  listThreadsMock.mockReset().mockResolvedValue({ threads: [] });
  getHistoryMock.mockReset().mockResolvedValue({ messages: [] });
  ensureQueryDataMock
    .mockReset()
    .mockImplementation(async (opts: { queryFn: () => unknown }) =>
      opts.queryFn(),
    );
  prefetchQueryMock
    .mockReset()
    .mockImplementation(async (opts: { queryFn: () => unknown }) =>
      opts.queryFn(),
    );
});

describe("loader de / e /session/$threadId — mesma query key, mesmo limit", () => {
  it("loader de '/' pede listThreads com THREAD_FETCH_LIMIT (não um limit truncado)", async () => {
    await (HomeRoute as unknown as LoaderOpts).loader!();

    expect(listThreadsMock).toHaveBeenCalledWith(THREAD_FETCH_LIMIT);
  });

  it("loader de '/session/$threadId' pede listThreads com o MESMO THREAD_FETCH_LIMIT do loader de '/'", async () => {
    await (HomeRoute as unknown as LoaderOpts).loader!();
    const homeLimit = listThreadsMock.mock.calls[0]?.[0];

    listThreadsMock.mockClear();
    await (SessionRoute as unknown as LoaderOpts).loader!({
      params: { threadId: "t1" },
    });
    const sessionLimit = listThreadsMock.mock.calls[0]?.[0];

    expect(sessionLimit).toBe(homeLimit);
    expect(sessionLimit).toBe(THREAD_FETCH_LIMIT);
  });

  it("loader de '/session/new' não tenta prefetch de histórico (thread ainda não existe)", async () => {
    await (SessionRoute as unknown as LoaderOpts).loader!({
      params: { threadId: "new" },
    });

    expect(getHistoryMock).not.toHaveBeenCalled();
    expect(listThreadsMock).toHaveBeenCalledWith(THREAD_FETCH_LIMIT);
  });

  it("falha em getHistory não bloqueia o loader quando listThreads teve sucesso (Promise.allSettled)", async () => {
    getHistoryMock.mockRejectedValue(new Error("rede instável"));
    prefetchQueryMock.mockImplementation(
      async (opts: { queryFn: () => unknown }) => {
        await opts.queryFn();
      },
    );

    await expect(
      (SessionRoute as unknown as LoaderOpts).loader!({
        params: { threadId: "t1" },
      }),
    ).resolves.not.toThrow();

    expect(listThreadsMock).toHaveBeenCalledWith(THREAD_FETCH_LIMIT);
  });

  it("loader não espera getHistory resolver — navegação não pode ficar presa no histórico pesado", async () => {
    let historyResolved = false;
    let resolveHistory!: () => void;
    const historyPromise = new Promise<void>((resolve) => {
      resolveHistory = () => {
        historyResolved = true;
        resolve();
      };
    });
    prefetchQueryMock.mockImplementation(async () => historyPromise);

    await (SessionRoute as unknown as LoaderOpts).loader!({
      params: { threadId: "t1" },
    });

    expect(historyResolved).toBe(false);
    resolveHistory();
    await historyPromise;
  });

  it("falha em listThreads (ensureQueryData rejeita) propaga — sem lista de threads não há sidebar navegável", async () => {
    ensureQueryDataMock.mockRejectedValue(new Error("backend indisponível"));

    await expect(
      (SessionRoute as unknown as LoaderOpts).loader!({
        params: { threadId: "t1" },
      }),
    ).rejects.toThrow("backend indisponível");
  });
});
