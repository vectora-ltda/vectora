// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getIdeLayoutState,
  getUnscaledViewportWidth,
  useMediaQuery,
} from "../use-media-query";

describe("getIdeLayoutState", () => {
  it.each([
    [639, "mobile"],
    [640, "wide"],
    [767, "wide"],
    [900, "wide"],
    [641, "wide"],
    [1024, "wide"],
    [1444, "wide"],
    [1920, "wide"],
  ])("classifica %s px como %s", (width, expected) => {
    expect(getIdeLayoutState(width)).toBe(expected);
  });
});

describe("getUnscaledViewportWidth", () => {
  afterEach(() => {
    delete (window as Window & { vectora?: unknown }).vectora;
  });

  it("usa a largura da janela Electron para ignorar a escala visual", () => {
    const originalOuterWidth = window.outerWidth;
    const originalInnerWidth = window.innerWidth;
    Object.defineProperties(window, {
      outerWidth: { configurable: true, value: 1444 },
      innerWidth: { configurable: true, value: 722 },
    });

    window.vectora = { windowControls: {} } as typeof window.vectora;
    expect(getUnscaledViewportWidth()).toBe(1444);

    Object.defineProperties(window, {
      outerWidth: { configurable: true, value: originalOuterWidth },
      innerWidth: { configurable: true, value: originalInnerWidth },
    });
  });

  it("usa a viewport útil no navegador, sem confiar em outerWidth", () => {
    Object.defineProperties(window, {
      outerWidth: { configurable: true, value: 1444 },
      innerWidth: { configurable: true, value: 722 },
    });

    expect(getUnscaledViewportWidth()).toBe(722);
  });
});

describe("useMediaQuery", () => {
  it("acompanha mudanças do matchMedia e remove o listener ao desmontar", () => {
    const originalMatchMedia = window.matchMedia;
    let listener: ((event: MediaQueryListEvent) => void) | undefined;
    const removeEventListener = vi.fn();
    window.matchMedia = vi.fn(() => ({
      matches: false,
      addEventListener: (_type: string, callback: EventListener) => {
        listener = callback as (event: MediaQueryListEvent) => void;
      },
      removeEventListener,
    })) as unknown as typeof window.matchMedia;

    try {
      const { result, unmount } = renderHook(() =>
        useMediaQuery("(min-width: 1px)"),
      );
      expect(result.current).toBe(false);

      act(() => listener?.({ matches: true } as MediaQueryListEvent));
      expect(result.current).toBe(true);

      unmount();
      expect(removeEventListener).toHaveBeenCalledOnce();
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });
});
