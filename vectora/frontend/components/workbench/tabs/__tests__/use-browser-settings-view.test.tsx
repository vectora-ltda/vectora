// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useBrowserSettingsView } from "../use-browser-settings-view";

vi.mock("@/lib/stores/settings-overlay-store", () => ({
  useSettingsOverlayStore: (selector: (state: { open: boolean }) => unknown) =>
    selector({ open: false }),
}));

afterEach(() => {
  vi.restoreAllMocks();
  Reflect.deleteProperty(window, "vectora");
});

function installBridge() {
  let handler:
    | ((viewId: number, event: { type: string; url?: string }) => void)
    | undefined;
  const bridge = {
    createView: vi.fn(async () => 7),
    destroyView: vi.fn(async () => undefined),
    navigate: vi.fn<
      (viewId: number, url: string) => Promise<{ ok: boolean; error?: string }>
    >(async () => ({ ok: true })),
    setBounds: vi.fn(),
    setVisible: vi.fn(),
    onEvent: vi.fn((next: typeof handler) => {
      handler = next;
      return () => {
        handler = undefined;
      };
    }),
  };
  Object.defineProperty(window, "vectora", {
    configurable: true,
    value: { browserView: bridge },
  });
  return {
    bridge,
    emit: (event: { type: string; url?: string }) => handler?.(7, event),
  };
}

function containerRef() {
  const container = document.createElement("div");
  vi.spyOn(container, "getBoundingClientRect").mockReturnValue({
    x: 10,
    y: 20,
    width: 320,
    height: 240,
    top: 20,
    right: 330,
    bottom: 260,
    left: 10,
    toJSON: () => ({}),
  });
  return { current: container };
}

describe("useBrowserSettingsView", () => {
  it("does not create a native view while closed", async () => {
    const native = installBridge();
    const ref = containerRef();
    renderHook(() =>
      useBrowserSettingsView({
        profileId: "profile-1",
        open: false,
        containerRef: ref,
      }),
    );
    await act(async () => Promise.resolve());
    expect(native.bridge.createView).not.toHaveBeenCalled();
  });

  it("destroys a view created after the hook was closed", async () => {
    const native = installBridge();
    let resolveCreate!: (id: number) => void;
    native.bridge.createView.mockReturnValue(
      new Promise<number>((resolve) => {
        resolveCreate = resolve;
      }),
    );
    const ref = containerRef();
    const { rerender } = renderHook(
      ({ open }) =>
        useBrowserSettingsView({
          profileId: "profile-1",
          open,
          containerRef: ref,
        }),
      { initialProps: { open: true } },
    );
    rerender({ open: false });
    await act(async () => {
      resolveCreate(7);
      await Promise.resolve();
    });
    expect(native.bridge.destroyView).toHaveBeenCalledWith(7);
  });

  it("confirms navigation, reports bounds and closes on native Escape", async () => {
    const native = installBridge();
    const ref = containerRef();
    const onClose = vi.fn();
    const { result } = renderHook(() =>
      useBrowserSettingsView({
        profileId: "profile-1",
        open: true,
        containerRef: ref,
        onClose,
      }),
    );
    await act(async () => Promise.resolve());
    await waitFor(() => expect(result.current.status).toBe("creating"));
    await act(async () => {
      native.emit({ type: "navigated", url: "chrome://settings" });
    });
    expect(result.current.status).toBe("confirmed");
    expect(native.bridge.setBounds).toHaveBeenCalledWith(7, {
      x: 10,
      y: 20,
      width: 320,
      height: 240,
    });
    expect(native.bridge.setVisible).toHaveBeenCalledWith(7, true);
    native.emit({ type: "escapePressed" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("destroys the view when navigation is rejected", async () => {
    const native = installBridge();
    native.bridge.navigate.mockResolvedValue({ ok: false, error: "blocked" });
    const ref = containerRef();
    const { result } = renderHook(() =>
      useBrowserSettingsView({
        profileId: "profile-1",
        open: true,
        containerRef: ref,
      }),
    );
    await waitFor(() => expect(result.current.status).toBe("failed"));
    expect(result.current.errorMessage).toBe("blocked");
    expect(native.bridge.destroyView).toHaveBeenCalledWith(7);
  });
});
