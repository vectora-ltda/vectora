// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getIdeLayoutState,
  getSessionLayoutState,
  getUnscaledViewportWidth,
  useMediaQuery,
  useSessionLayoutState,
} from "../use-media-query";

describe("getSessionLayoutState", () => {
  it.each([
    [320, "mobile"],
    [639, "mobile"],
    [640, "tablet"],
    [767, "tablet"],
    [1023, "tablet"],
    [1024, "wide"],
    [1440, "wide"],
  ])("classifica %s px como %s", (width, expected) => {
    expect(getSessionLayoutState(width)).toBe(expected);
  });
});

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

describe("useSessionLayoutState", () => {
  it("atualiza em resize da janela e do visualViewport e remove os listeners", () => {
    const originalInnerWidth = window.innerWidth;
    const originalVisualViewport = window.visualViewport;
    const windowListener = vi.fn();
    const visualListener = vi.fn();
    let onWindowResize: (() => void) | undefined;
    let onVisualResize: (() => void) | undefined;
    const viewport = { width: 800 };
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 800,
    });
    Object.defineProperty(window, "visualViewport", {
      configurable: true,
      value: {
        get width() {
          return viewport.width;
        },
        addEventListener: (_type: string, callback: () => void) => {
          onVisualResize = callback;
          visualListener.mockImplementation(() => callback());
        },
        removeEventListener: visualListener,
      },
    });
    const originalAdd = window.addEventListener;
    const originalRemove = window.removeEventListener;
    window.addEventListener = ((type: string, callback: EventListener) => {
      if (type === "resize") onWindowResize = callback as () => void;
      originalAdd.call(window, type, callback);
    }) as typeof window.addEventListener;
    window.removeEventListener = ((type: string, callback: EventListener) => {
      if (type === "resize") windowListener();
      originalRemove.call(window, type, callback);
    }) as typeof window.removeEventListener;

    try {
      const { result, unmount } = renderHook(() => useSessionLayoutState());
      expect(result.current).toBe("tablet");

      Object.defineProperty(window, "innerWidth", {
        configurable: true,
        value: 1200,
      });
      viewport.width = 1200;
      act(() => onWindowResize?.());
      expect(result.current).toBe("wide");

      Object.defineProperty(window, "innerWidth", {
        configurable: true,
        value: 500,
      });
      viewport.width = 500;
      act(() => onVisualResize?.());
      expect(result.current).toBe("mobile");

      unmount();
      expect(windowListener).toHaveBeenCalledOnce();
      expect(visualListener).toHaveBeenCalledOnce();
    } finally {
      window.addEventListener = originalAdd;
      window.removeEventListener = originalRemove;
      Object.defineProperty(window, "innerWidth", {
        configurable: true,
        value: originalInnerWidth,
      });
      Object.defineProperty(window, "visualViewport", {
        configurable: true,
        value: originalVisualViewport,
      });
    }
  });
});
