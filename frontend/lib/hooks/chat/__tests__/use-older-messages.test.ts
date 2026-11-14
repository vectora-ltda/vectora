// @vitest-environment jsdom
/**
 * useOlderMessages
 *
 * Hook para carregar mensagens mais antigas de uma thread quando o utilizador
 * chegar ao topo da lista (IntersectionObserver).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useOlderMessages } from "../use-older-messages";

// Mock do client API
vi.mock("@/lib/api/vectora-client", () => ({
  getHistoryPage: vi.fn(),
}));

import type { PagedHistoryResponse } from "@/lib/api/vectora-client";
import { getHistoryPage } from "@/lib/api/vectora-client";

const mockGetHistoryPage = vi.mocked(getHistoryPage);

// IntersectionObserver mock simples
class MockIntersectionObserver {
  static instances: MockIntersectionObserver[] = [];
  callback: IntersectionObserverCallback;
  constructor(cb: IntersectionObserverCallback) {
    this.callback = cb;
    MockIntersectionObserver.instances.push(this);
  }
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  triggerIntersect(isIntersecting = true) {
    this.callback(
      [{ isIntersecting, target: {} as Element } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    );
  }
}

beforeEach(() => {
  MockIntersectionObserver.instances = [];
  vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
  mockGetHistoryPage.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useOlderMessages", () => {
  it("retorna hasMore=false e isLoading=false inicialmente", () => {
    const sentinelRef = { current: null as Element | null };
    const { result } = renderHook(() =>
      useOlderMessages("thread1", sentinelRef, 0),
    );

    expect(result.current.hasMore).toBe(false);
    expect(result.current.isLoadingOlder).toBe(false);
  });

  it("não faz fetch quando hasMore=false e o sentinel intersecta", async () => {
    const el = {} as Element;
    const sentinelRef = { current: el };

    renderHook(() => useOlderMessages("thread1", sentinelRef, 0));

    const observer = MockIntersectionObserver.instances[0];
    if (observer) {
      await act(async () => {
        observer.triggerIntersect(true);
      });
    }

    expect(mockGetHistoryPage).not.toHaveBeenCalled();
  });

  it("chama getHistoryPage quando hasMore=true e o sentinel intersecta", async () => {
    mockGetHistoryPage.mockResolvedValueOnce({
      messages: [{ role: "human", content: "old msg" }],
      has_more: false,
      total_count: 1,
    });

    const el = {} as Element;
    const sentinelRef = { current: el };

    const { result } = renderHook(() =>
      useOlderMessages("thread1", sentinelRef, 5, true),
    );

    const observer = MockIntersectionObserver.instances[0];
    if (observer) {
      await act(async () => {
        observer.triggerIntersect(true);
      });
    }

    expect(mockGetHistoryPage).toHaveBeenCalledWith(
      "thread1",
      50,
      expect.any(Number),
      undefined,
    );
    expect(result.current.hasMore).toBe(false);
  });

  it("atualiza hasMore=true quando o servidor diz que há mais", async () => {
    mockGetHistoryPage.mockResolvedValueOnce({
      messages: [{ role: "human", content: "msg" }],
      has_more: true,
      total_count: 100,
    });

    const el = {} as Element;
    const sentinelRef = { current: el };

    const { result } = renderHook(() =>
      useOlderMessages("thread1", sentinelRef, 5, true),
    );

    const observer = MockIntersectionObserver.instances[0];
    if (observer) {
      await act(async () => {
        observer.triggerIntersect(true);
      });
    }

    expect(result.current.hasMore).toBe(true);
  });

  it("não faz fetch duplo enquanto isLoadingOlder=true", async () => {
    const ctrl: { resolve?: (v: PagedHistoryResponse) => void } = {};
    mockGetHistoryPage.mockImplementationOnce(
      () =>
        new Promise<PagedHistoryResponse>((res) => {
          ctrl.resolve = res;
        }),
    );

    const el = {} as Element;
    const sentinelRef = { current: el };

    renderHook(() => useOlderMessages("thread1", sentinelRef, 5, true));

    const observer = MockIntersectionObserver.instances[0];
    if (observer) {
      // Dispara primeiro intersect (loading = true)
      act(() => observer.triggerIntersect(true));
      // Dispara segundo intersect enquanto ainda carrega
      act(() => observer.triggerIntersect(true));
    }

    // Resolve dentro de act para que a atualização de estado seja capturada
    await act(async () => {
      ctrl.resolve?.({ messages: [], has_more: false, total_count: 0 });
    });

    // Deve ter chamado apenas uma vez
    expect(mockGetHistoryPage).toHaveBeenCalledTimes(1);
  });
});

const intersect = async () => {
  const observers = MockIntersectionObserver.instances;
  await act(async () => {
    observers[observers.length - 1]?.triggerIntersect(true);
  });
};

describe("useOlderMessages — cursor por message_id", () => {
  const sentinelRef = { current: {} as Element };

  it("a primeira página usa o offset e as seguintes usam o cursor devolvido", async () => {
    mockGetHistoryPage
      .mockResolvedValueOnce({
        messages: [{ role: "human", content: "a" }],
        has_more: true,
        total_count: 100,
        next_cursor: 41,
      })
      .mockResolvedValueOnce({
        messages: [{ role: "human", content: "b" }],
        has_more: false,
        total_count: 100,
        next_cursor: null,
      });
    renderHook(() => useOlderMessages("t1", sentinelRef, 5, true));

    await intersect();
    await intersect();

    expect(mockGetHistoryPage).toHaveBeenNthCalledWith(
      1,
      "t1",
      50,
      5,
      undefined,
    );
    expect(mockGetHistoryPage).toHaveBeenNthCalledWith(2, "t1", 50, 6, 41);
  });

  it("cursor removido no servidor refaz a página por offset", async () => {
    const { HistoryCursorGoneError } = await import("@/lib/api/history-cursor");
    mockGetHistoryPage
      .mockResolvedValueOnce({
        messages: [{ role: "human", content: "a" }],
        has_more: true,
        total_count: 100,
        next_cursor: 41,
      })
      .mockRejectedValueOnce(new HistoryCursorGoneError())
      .mockResolvedValueOnce({
        messages: [{ role: "human", content: "b" }],
        has_more: false,
        total_count: 100,
        next_cursor: null,
      });
    const { result } = renderHook(() =>
      useOlderMessages("t1", sentinelRef, 5, true),
    );

    await intersect();
    await intersect();

    expect(mockGetHistoryPage).toHaveBeenNthCalledWith(3, "t1", 50, 6);
    expect(result.current.olderMessages.map((m) => m.content)).toEqual([
      "b",
      "a",
    ]);
  });

  it("o cursor de uma thread não vale para outra", async () => {
    mockGetHistoryPage.mockResolvedValue({
      messages: [{ role: "human", content: "a" }],
      has_more: true,
      total_count: 100,
      next_cursor: 41,
    });
    const { rerender } = renderHook(
      ({ id }) => useOlderMessages(id, sentinelRef, 5, true),
      { initialProps: { id: "t1" } },
    );
    await intersect();

    rerender({ id: "t2" });
    await intersect();

    expect(mockGetHistoryPage).toHaveBeenLastCalledWith("t2", 50, 5, undefined);
  });
});

describe("useOlderMessages — cursor inicial e nova tentativa", () => {
  const sentinelRef = { current: {} as Element };

  it("a primeira página anterior já usa o cursor da página inicial", async () => {
    mockGetHistoryPage.mockResolvedValueOnce({
      messages: [{ role: "human", content: "a" }],
      has_more: false,
      total_count: 100,
      next_cursor: null,
    });
    renderHook(() => useOlderMessages("t1", sentinelRef, 100, true, 901));

    await intersect();

    expect(mockGetHistoryPage).toHaveBeenCalledWith("t1", 50, 100, 901);
  });

  it("falha marca loadFailed e a chamada manual tenta de novo", async () => {
    mockGetHistoryPage
      .mockRejectedValueOnce(new Error("rede"))
      .mockResolvedValueOnce({
        messages: [{ role: "human", content: "a" }],
        has_more: false,
        total_count: 3,
        next_cursor: null,
      });
    const { result } = renderHook(() =>
      useOlderMessages("t1", sentinelRef, 2, true),
    );

    await intersect();
    expect(result.current.loadFailed).toBe(true);
    expect(result.current.olderMessages).toEqual([]);

    await act(async () => {
      await result.current.loadOlder();
    });

    expect(result.current.loadFailed).toBe(false);
    expect(result.current.olderMessages.map((m) => m.content)).toEqual(["a"]);
    expect(mockGetHistoryPage).toHaveBeenCalledTimes(2);
  });

  it("depois de falhar o observador não dispara em laço", async () => {
    mockGetHistoryPage.mockRejectedValue(new Error("rede"));
    renderHook(() => useOlderMessages("t1", sentinelRef, 2, true));

    await intersect();
    await act(async () => {});

    // O observador do topo é desligado e não é recriado: só o botão tenta de novo.
    const observers = MockIntersectionObserver.instances;
    expect(
      observers.every((observer) => observer.disconnect.mock.calls.length > 0),
    ).toBe(true);
    expect(mockGetHistoryPage).toHaveBeenCalledTimes(1);
  });
});
