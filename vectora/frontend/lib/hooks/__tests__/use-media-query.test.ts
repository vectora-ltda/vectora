// @vitest-environment jsdom
/**
 * Layouts multi-painel (workbench modo IDE) precisam saber quando a viewport
 * cruza o breakpoint `md` do Tailwind para colapsar em um único painel
 * visível por vez. `useMediaQuery` encapsula esse detector via
 * `matchMedia`, já que jsdom não o implementa nativamente.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

import {
  useMediaQuery,
  useIsNarrowViewport,
} from "@/lib/hooks/use-media-query";

interface FakeMql {
  matches: boolean;
  media: string;
  listeners: Set<(e: MediaQueryListEvent) => void>;
  addEventListener: (
    type: "change",
    cb: (e: MediaQueryListEvent) => void,
  ) => void;
  removeEventListener: (
    type: "change",
    cb: (e: MediaQueryListEvent) => void,
  ) => void;
}

function installFakeMatchMedia(initialMatches: boolean) {
  const mql: FakeMql = {
    matches: initialMatches,
    media: "",
    listeners: new Set(),
    addEventListener: (_type, cb) => mql.listeners.add(cb),
    removeEventListener: (_type, cb) => mql.listeners.delete(cb),
  };
  window.matchMedia = vi
    .fn()
    .mockReturnValue(mql) as unknown as typeof window.matchMedia;

  return {
    mql,
    setMatches(next: boolean) {
      mql.matches = next;
      for (const cb of mql.listeners) {
        cb({ matches: next } as MediaQueryListEvent);
      }
    },
  };
}

describe("useMediaQuery", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("retorna o valor inicial de matches e atualiza quando a media query muda", () => {
    const fake = installFakeMatchMedia(false);
    const { result } = renderHook(() => useMediaQuery("(max-width: 767px)"));

    expect(result.current).toBe(false);

    act(() => fake.setMatches(true));
    expect(result.current).toBe(true);

    act(() => fake.setMatches(false));
    expect(result.current).toBe(false);
  });

  it("desmonta sem deixar o listener registrado (sem vazamento)", () => {
    const fake = installFakeMatchMedia(false);
    const { unmount } = renderHook(() => useMediaQuery("(max-width: 767px)"));

    expect(fake.mql.listeners.size).toBe(1);
    unmount();
    expect(fake.mql.listeners.size).toBe(0);
  });

  it("useIsNarrowViewport colapsa o IDE abaixo de 988px", () => {
    const fake = installFakeMatchMedia(true);
    const { result } = renderHook(() => useIsNarrowViewport());

    expect(result.current).toBe(true);
    expect(window.matchMedia).toHaveBeenCalledWith("(max-width: 987px)");

    act(() => fake.setMatches(false));
    expect(result.current).toBe(false);
  });
});
