// @vitest-environment jsdom
/**
 * useSessionExpiry — agenda um toast de aviso para `exp - 5min` lido de
 * `user.token_expires_at` (auth-store). Clampa o delay entre MIN (1s) e
 * MAX (24h) e oferece ação "Renovar" que chama /auth/refresh + /auth/me.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, cleanup } from "@testing-library/react";

import { useAuthStore } from "@/lib/stores/auth-store";
import type { AuthUser } from "@/lib/types/auth";

const toastWarning = vi.fn();
const toastSuccess = vi.fn();
const toastError = vi.fn();

vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: {
    getState: () => ({
      warning: toastWarning,
      success: toastSuccess,
      error: toastError,
    }),
  },
}));

import { useSessionExpiry } from "@/lib/hooks/use-session-expiry";

function setExp(secondsFromNow: number | null): void {
  useAuthStore.setState({
    user:
      secondsFromNow === null
        ? null
        : ({
            token_expires_at: Math.floor(Date.now() / 1000) + secondsFromNow,
          } as unknown as AuthUser),
    isAuthenticated: secondsFromNow !== null,
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  toastWarning.mockReset();
  toastSuccess.mockReset();
  toastError.mockReset();
  useAuthStore.setState({ user: null, isAuthenticated: false });
});

afterEach(() => {
  cleanup(); // desmonta hooks (limpa seus timers/efeitos) antes do próximo teste
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useSessionExpiry — agendamento", () => {
  it("não agenda quando não há token", () => {
    renderHook(() => useSessionExpiry());
    act(() => {
      vi.advanceTimersByTime(60 * 60 * 1000);
    });
    expect(toastWarning).not.toHaveBeenCalled();
  });

  it("dispara o toast em exp - 5min", () => {
    setExp(20 * 60);
    renderHook(() => useSessionExpiry());
    act(() => {
      vi.advanceTimersByTime(15 * 60 * 1000 + 100);
    });
    expect(toastWarning).toHaveBeenCalledTimes(1);
  });

  it("não dispara antes do horário agendado", () => {
    setExp(20 * 60);
    renderHook(() => useSessionExpiry());
    act(() => {
      vi.advanceTimersByTime(10 * 60 * 1000);
    });
    expect(toastWarning).not.toHaveBeenCalled();
  });

  it("token quase expirando agenda com o delay mínimo (1s)", () => {
    setExp(60);
    renderHook(() => useSessionExpiry());
    act(() => {
      vi.advanceTimersByTime(1100);
    });
    expect(toastWarning).toHaveBeenCalledTimes(1);
  });

  it("token já expirado dispara após o delay mínimo", () => {
    setExp(-100);
    renderHook(() => useSessionExpiry());
    act(() => {
      vi.advanceTimersByTime(1100);
    });
    expect(toastWarning).toHaveBeenCalled();
  });

  it("exp muito distante é clampado em MAX (24h) — não dispara antes", () => {
    setExp(48 * 60 * 60);
    renderHook(() => useSessionExpiry());
    act(() => {
      vi.advanceTimersByTime(23 * 60 * 60 * 1000);
    });
    expect(toastWarning).not.toHaveBeenCalled();
  });

  it("mesmo exp não reagenda (dispara só uma vez)", () => {
    setExp(20 * 60);
    const { rerender } = renderHook(() => useSessionExpiry());
    act(() => {
      vi.advanceTimersByTime(5 * 60 * 1000);
    });
    rerender();
    act(() => {
      vi.advanceTimersByTime(11 * 60 * 1000);
    });
    expect(toastWarning).toHaveBeenCalledTimes(1);
  });

  it("token removido cancela o agendamento", () => {
    setExp(20 * 60);
    renderHook(() => useSessionExpiry());
    act(() => {
      setExp(null);
    });
    act(() => {
      vi.advanceTimersByTime(20 * 60 * 1000);
    });
    expect(toastWarning).not.toHaveBeenCalled();
  });

  it("token novo reagenda para o novo exp", () => {
    setExp(60 * 60);
    renderHook(() => useSessionExpiry());
    act(() => {
      setExp(20 * 60);
    });
    act(() => {
      vi.advanceTimersByTime(15 * 60 * 1000 + 100);
    });
    expect(toastWarning).toHaveBeenCalledTimes(1);
  });

  it("unmount limpa o timer (não dispara depois)", () => {
    setExp(20 * 60);
    const { unmount } = renderHook(() => useSessionExpiry());
    unmount();
    act(() => {
      vi.advanceTimersByTime(20 * 60 * 1000);
    });
    expect(toastWarning).not.toHaveBeenCalled();
  });

  it("o toast é persistente (duration null) com ação", () => {
    setExp(20 * 60);
    renderHook(() => useSessionExpiry());
    act(() => {
      vi.advanceTimersByTime(15 * 60 * 1000 + 100);
    });
    const opts = toastWarning.mock.calls[0][1] as {
      duration: number | null;
      action: { onClick: () => void };
    };
    expect(opts.duration).toBeNull();
    expect(typeof opts.action.onClick).toBe("function");
  });
});

function fireToast(): { onClick: () => Promise<void> } {
  setExp(20 * 60);
  renderHook(() => useSessionExpiry());
  act(() => {
    vi.advanceTimersByTime(15 * 60 * 1000 + 100);
  });
  return (
    toastWarning.mock.calls[0][1] as {
      action: { onClick: () => Promise<void> };
    }
  ).action;
}

describe("useSessionExpiry — ação Renovar", () => {
  it("Renovar com refresh+me ok mostra toast de sucesso", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({ ok: true, json: async () => ({ id: "u1" }) }) as Response,
      ),
    );
    const action = fireToast();
    await act(async () => {
      await action.onClick();
    });
    expect(toastSuccess).toHaveBeenCalled();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });

  it("Renovar com refresh falho mostra toast de erro", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false }) as Response),
    );
    const action = fireToast();
    await act(async () => {
      await action.onClick();
    });
    expect(toastError).toHaveBeenCalled();
  });
});
