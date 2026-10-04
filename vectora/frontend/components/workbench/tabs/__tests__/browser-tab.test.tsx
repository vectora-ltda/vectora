// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { BrowserTab, clearBrowserSessionCache } from "../browser-tab";
import { useSettingsStore } from "@/lib/stores/settings-store";
vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_target, key) => () => String(key) }),
}));
vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (selector: (value: unknown) => unknown) =>
    selector({ getActive: () => ({ id: "w" }) }),
}));
vi.mock("@/components/workbench/settings/workbench-settings-surface", () => ({
  WorkbenchSettingsSurface: () => null,
}));
vi.mock("../browser-devtools-panel", () => ({
  BrowserDevtoolsPanel: () => null,
}));
afterEach(() => {
  cleanup();
  clearBrowserSessionCache();
  delete window.vectora;
  vi.unstubAllGlobals();
});
describe("browser settings changes", () => {
  it("uses the current search engine for newly requested tabs", async () => {
    let onEvent:
      ((id: number, event: { type: string; url: string }) => void) | undefined;
    let id = 0;
    const navigate = vi.fn().mockResolvedValue({ ok: true });
    const bridge = {
      createView: vi.fn(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
        return ++id;
      }),
      destroyView: vi.fn(),
      setVisible: vi.fn(),
      setBounds: vi.fn(),
      setPolicy: vi.fn(),
      setZoom: vi.fn(),
      navigate,
      onEvent: (handler: typeof onEvent) => {
        onEvent = handler;
        return () => {
          onEvent = undefined;
        };
      },
    };
    Object.defineProperty(window, "vectora", {
      configurable: true,
      value: { browserView: bridge },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ configurations: [], servers: [] })),
      ),
    );
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    useSettingsStore.setState({ browserSearchEngine: "duckduckgo" });
    render(<BrowserTab threadId="t" />);
    await waitFor(() => expect(onEvent).toBeDefined());
    await act(async () => {
      useSettingsStore.setState({ browserSearchEngine: "google" });
    });
    await act(async () => {
      onEvent?.(1, { type: "popupRequested", url: "search words" });
    });
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(
        expect.any(Number),
        "https://www.google.com/search?q=search%20words",
      ),
    );
  });
});
