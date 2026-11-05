// @vitest-environment jsdom
/**
 * useDelayedLoading — atrasa o skeleton por delayMs para evitar flash.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

import { useDelayedLoading } from "@/lib/hooks/use-delayed-loading";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useDelayedLoading", () => {
  it("retorna false imediatamente quando não está carregando", () => {
    const { result } = renderHook(() => useDelayedLoading(false));
    expect(result.current).toBe(false);
  });

  it("retorna false antes do delay mesmo carregando", () => {
    const { result } = renderHook(() => useDelayedLoading(true));
    act(() => {
      vi.advanceTimersByTime(50);
    });
    expect(result.current).toBe(false);
  });

  it("retorna true após o delay padrão (100ms)", () => {
    const { result } = renderHook(() => useDelayedLoading(true));
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe(true);
  });

  it("respeita delayMs custom", () => {
    const { result } = renderHook(() => useDelayedLoading(true, 300));
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(result.current).toBe(false);
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe(true);
  });

  it("não mostra se isLoading vira false antes do delay", () => {
    const { result, rerender } = renderHook(({ l }) => useDelayedLoading(l), {
      initialProps: { l: true },
    });
    act(() => {
      vi.advanceTimersByTime(50);
    });
    rerender({ l: false });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current).toBe(false);
  });

  it("esconde imediatamente quando isLoading vira false após mostrar", () => {
    const { result, rerender } = renderHook(({ l }) => useDelayedLoading(l), {
      initialProps: { l: true },
    });
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(result.current).toBe(true);
    rerender({ l: false });
    expect(result.current).toBe(false);
  });

  it("delayMs 0 mostra no próximo tick", () => {
    const { result } = renderHook(() => useDelayedLoading(true, 0));
    act(() => {
      vi.advanceTimersByTime(0);
    });
    expect(result.current).toBe(true);
  });

  it("ciclo true→false→true volta a atrasar", () => {
    const { result, rerender } = renderHook(({ l }) => useDelayedLoading(l), {
      initialProps: { l: true },
    });
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(result.current).toBe(true);
    rerender({ l: false });
    expect(result.current).toBe(false);
    rerender({ l: true });
    expect(result.current).toBe(false);
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(result.current).toBe(true);
  });

  it("unmount não lança e limpa o timer", () => {
    const { unmount } = renderHook(() => useDelayedLoading(true));
    expect(() => {
      unmount();
      vi.advanceTimersByTime(200);
    }).not.toThrow();
  });

  it("não mostra 1ms antes do delay", () => {
    const { result } = renderHook(() => useDelayedLoading(true, 100));
    act(() => {
      vi.advanceTimersByTime(99);
    });
    expect(result.current).toBe(false);
  });

  it("mostra exatamente no delay", () => {
    const { result } = renderHook(() => useDelayedLoading(true, 100));
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe(true);
  });

  it("rerender com mesmo isLoading mantém o estado", () => {
    const { result, rerender } = renderHook(({ l }) => useDelayedLoading(l), {
      initialProps: { l: true },
    });
    act(() => {
      vi.advanceTimersByTime(150);
    });
    rerender({ l: true });
    expect(result.current).toBe(true);
  });
});
