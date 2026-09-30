// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useBrowserSettingsView } from "../use-browser-settings-view";
import type { VectoraBrowserViewEvent } from "@/lib/types/vectora-bridge";

class ResizeObserverStub {
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();
}

afterEach(() => {
  vi.useRealTimers();
  delete window.vectora;
  vi.restoreAllMocks();
});

function setupBridge() {
  let listener:
    ((viewId: number, event: VectoraBrowserViewEvent) => void) | undefined;
  const bridge = {
    createView: vi.fn().mockResolvedValue(42),
    destroyView: vi.fn(),
    navigate: vi.fn().mockResolvedValue({ ok: true }),
    goBack: vi.fn(),
    goForward: vi.fn(),
    reload: vi.fn(),
    stop: vi.fn(),
    setBounds: vi.fn(),
    setVisible: vi.fn(),
    clearProfileData: vi.fn().mockResolvedValue(undefined),
    onEvent: vi.fn((handler: typeof listener) => {
      listener = handler;
      return () => {
        listener = undefined;
      };
    }),
  };
  window.vectora = { browserView: bridge };
  return {
    bridge,
    emit(event: VectoraBrowserViewEvent) {
      listener?.(42, event);
    },
  };
}

function containerRef() {
  const element = document.createElement("div");
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({
    x: 10,
    y: 20,
    left: 10,
    top: 20,
    right: 210,
    bottom: 120,
    width: 200,
    height: 100,
    toJSON: () => ({}),
  });
  return { current: element };
}

describe("useBrowserSettingsView", () => {
  it("cria a view nativa, aplica bounds e fecha com Escape", async () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    const { bridge, emit } = setupBridge();
    const onClose = vi.fn();
    const { result } = renderHook(() =>
      useBrowserSettingsView({
        profileId: "profile-a",
        open: true,
        visible: true,
        settingsOverlayOpen: false,
        containerRef: containerRef(),
        onClose,
      }),
    );

    await waitFor(() => expect(result.current.viewId).toBe(42));
    expect(bridge.createView).toHaveBeenCalledWith({
      profileId: "profile-a",
      kind: "native-settings",
    });
    expect(bridge.navigate).toHaveBeenCalledWith(42, "chrome://settings");
    expect(bridge.setBounds).toHaveBeenCalledWith(42, {
      x: 10,
      y: 20,
      width: 200,
      height: 100,
    });
    act(() =>
      emit({
        type: "navigated",
        url: "chrome://settings",
        canGoBack: false,
        canGoForward: false,
      }),
    );
    act(() => emit({ type: "escapePressed" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("destrói a view quando não há confirmação da rota", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    const { bridge } = setupBridge();
    const { result } = renderHook(() =>
      useBrowserSettingsView({
        profileId: "profile-a",
        open: true,
        visible: true,
        settingsOverlayOpen: false,
        containerRef: containerRef(),
        onClose: vi.fn(),
      }),
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.viewId).toBe(42);
    act(() => vi.advanceTimersByTime(5_000));
    expect(bridge.destroyView).toHaveBeenCalledWith(42);
    expect(result.current.error).toBe(true);
  });

  it("trata loadFailed como erro e remove a view", async () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    const { bridge, emit } = setupBridge();
    const { result } = renderHook(() =>
      useBrowserSettingsView({
        profileId: "profile-a",
        open: true,
        visible: true,
        settingsOverlayOpen: false,
        containerRef: containerRef(),
        onClose: vi.fn(),
      }),
    );

    await waitFor(() => expect(result.current.viewId).toBe(42));
    act(() =>
      emit({
        type: "loadFailed",
        errorCode: -6,
        errorDescription: "ERR_FILE_NOT_FOUND",
        url: "chrome://settings",
      }),
    );
    expect(bridge.destroyView).toHaveBeenCalledWith(42);
    expect(result.current.error).toBe(true);
  });
});
