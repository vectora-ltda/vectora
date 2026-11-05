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
