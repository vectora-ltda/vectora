// @vitest-environment jsdom
/**
 * Tests para hooks de UI: useDelayedLoading (debounce de skeleton),
 * useHydrated (flag pós-mount) e useNetworkStatus (online/offline + SSE).
 */

import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDelayedLoading } from "../use-delayed-loading";
import { useHydrated } from "../use-hydrated";
import { useNetworkStatus, useNetworkStore } from "../use-network-status";

describe("useDelayedLoading", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("não mostra quando não está carregando", () => {
    const { result } = renderHook(() => useDelayedLoading(false, 100));
    expect(result.current).toBe(false);
  });

  it("só mostra após o delay enquanto carregando", () => {
    const { result } = renderHook(() => useDelayedLoading(true, 100));
    expect(result.current).toBe(false); // antes do delay
    act(() => {
      vi.advanceTimersByTime(120);
    });
    expect(result.current).toBe(true);
  });

  it("não mostra se o carregamento termina antes do delay", () => {
    const { result, rerender } = renderHook(
      ({ loading }) => useDelayedLoading(loading, 100),
      { initialProps: { loading: true } },
    );
    act(() => {
      vi.advanceTimersByTime(50);
    });
    rerender({ loading: false });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current).toBe(false);
  });
});

describe("useHydrated", () => {
  it("vira true após o mount", () => {
    const { result } = renderHook(() => useHydrated());
    expect(result.current).toBe(true);
  });
});

describe("useNetworkStatus", () => {
  beforeEach(() => useNetworkStore.setState({ sseStatus: "idle" }));

  it("reflete eventos offline/online do navegador", () => {
    const { result } = renderHook(() => useNetworkStatus());
    expect(result.current.offline).toBe(false);
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(result.current.offline).toBe(true);
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(result.current.offline).toBe(false);
  });

  it("reflete sseStatus 'reconnecting' da store", () => {
    const { result } = renderHook(() => useNetworkStatus());
    expect(result.current.sseReconnecting).toBe(false);
    act(() => useNetworkStore.getState().setSSEStatus("reconnecting"));
    expect(result.current.sseReconnecting).toBe(true);
    expect(result.current.sseStatus).toBe("reconnecting");
  });
});
