// @vitest-environment jsdom
/**
 * useNetworkStatus + useNetworkStore — status combinado de rede (online/offline
 * do navegador + estado SSE publicado por consumidores do EventSource).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

import {
  useNetworkStatus,
  useNetworkStore,
} from "@/lib/hooks/use-network-status";

beforeEach(() => {
  useNetworkStore.setState({ sseStatus: "idle" });
});

describe("useNetworkStore", () => {
  it("começa em idle", () => {
    expect(useNetworkStore.getState().sseStatus).toBe("idle");
  });

  it("setSSEStatus atualiza o status", () => {
    useNetworkStore.getState().setSSEStatus("connected");
    expect(useNetworkStore.getState().sseStatus).toBe("connected");
  });

  it("aceita todos os status válidos", () => {
    for (const s of ["idle", "connected", "reconnecting", "failed"] as const) {
      useNetworkStore.getState().setSSEStatus(s);
      expect(useNetworkStore.getState().sseStatus).toBe(s);
    }
  });
});

describe("useNetworkStatus", () => {
  it("offline inicial é false quando navigator está online", () => {
    const { result } = renderHook(() => useNetworkStatus());
    expect(result.current.offline).toBe(false);
  });

  it("evento offline do navegador seta offline true", () => {
    const { result } = renderHook(() => useNetworkStatus());
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(result.current.offline).toBe(true);
  });

  it("evento online do navegador seta offline false", () => {
    const { result } = renderHook(() => useNetworkStatus());
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(result.current.offline).toBe(false);
  });

  it("sseReconnecting true quando a store está reconnecting", () => {
    const { result } = renderHook(() => useNetworkStatus());
    act(() => {
      useNetworkStore.getState().setSSEStatus("reconnecting");
    });
    expect(result.current.sseReconnecting).toBe(true);
  });

  it("sseReconnecting false para connected", () => {
    const { result } = renderHook(() => useNetworkStatus());
    act(() => {
      useNetworkStore.getState().setSSEStatus("connected");
    });
    expect(result.current.sseReconnecting).toBe(false);
  });

  it("sseStatus reflete o valor da store", () => {
    const { result } = renderHook(() => useNetworkStatus());
    act(() => {
      useNetworkStore.getState().setSSEStatus("failed");
    });
    expect(result.current.sseStatus).toBe("failed");
  });

  it("idle não é reconnecting", () => {
    const { result } = renderHook(() => useNetworkStatus());
    expect(result.current.sseReconnecting).toBe(false);
  });

  it("offline inicial é true quando navigator.onLine é false", () => {
    const orig = Object.getOwnPropertyDescriptor(window.navigator, "onLine");
    Object.defineProperty(window.navigator, "onLine", {
      configurable: true,
      get: () => false,
    });
    try {
      const { result } = renderHook(() => useNetworkStatus());
      expect(result.current.offline).toBe(true);
    } finally {
      if (orig) Object.defineProperty(window.navigator, "onLine", orig);
    }
  });

  it("remove os listeners no unmount (sem vazar)", () => {
    const remove = vi.spyOn(window, "removeEventListener");
    const { unmount } = renderHook(() => useNetworkStatus());
    unmount();
    expect(remove).toHaveBeenCalledWith("online", expect.any(Function));
    expect(remove).toHaveBeenCalledWith("offline", expect.any(Function));
    remove.mockRestore();
  });

  it("múltiplos hooks compartilham o sseStatus da store", () => {
    const a = renderHook(() => useNetworkStatus());
    const b = renderHook(() => useNetworkStatus());
    act(() => {
      useNetworkStore.getState().setSSEStatus("reconnecting");
    });
    expect(a.result.current.sseReconnecting).toBe(true);
    expect(b.result.current.sseReconnecting).toBe(true);
  });
});
