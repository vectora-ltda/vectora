// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { BrowserSettingsContent } from "../browser-settings-content";

type BrowserViewEvent =
  | {
      type: "navigated";
      url: string;
      canGoBack: boolean;
      canGoForward: boolean;
    }
  | { type: "escapePressed" };

const setOpen = vi.fn();
const openBrowserSettings = vi.fn();

vi.mock("@/lib/stores/settings-overlay-store", () => ({
  useSettingsOverlayStore: {
    getState: () => ({ setOpen }),
  },
}));

vi.mock("@/lib/stores/workbench-store", () => ({
  useWorkbenchStore: {
    getState: () => ({ openBrowserSettings }),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  Reflect.deleteProperty(window, "vectora");
});

function installBridge() {
  let eventHandler: ((viewId: number, event: BrowserViewEvent) => void) | null =
    null;
  const bridge = {
    createView: vi.fn(async () => 42),
    destroyView: vi.fn(),
    navigate: vi.fn(async () => ({ ok: true })),
    setBounds: vi.fn(),
    setVisible: vi.fn(),
    onEvent: vi.fn((handler: typeof eventHandler) => {
      eventHandler = handler;
      return () => {
        eventHandler = null;
      };
    }),
  };
  Object.defineProperty(window, "vectora", {
    configurable: true,
    value: { browserView: bridge },
  });
  return {
    bridge,
    emit(event: BrowserViewEvent) {
      eventHandler?.(42, event);
    },
  };
}

describe("BrowserSettingsContent", () => {
  it("cria, navega, dimensiona e destrói a view nativa", async () => {
    const native = installBridge();
    const onRequestClose = vi.fn();
    const { unmount } = render(
      <BrowserSettingsContent
        threadId="thread-1"
        workspaceId="workspace-1"
        browserProfileId="session-profile"
        presentation="workbench"
        onRequestClose={onRequestClose}
      />,
    );

    await waitFor(() =>
      expect(native.bridge.createView).toHaveBeenCalledWith({
        profileId: "session-profile",
        kind: "native-settings",
      }),
    );
    expect(native.bridge.navigate).toHaveBeenCalledWith(
      42,
      "chrome://settings",
    );
    await act(async () => {
      native.emit({
        type: "navigated",
        url: "chrome://settings",
        canGoBack: false,
        canGoForward: false,
      });
    });
    await waitFor(() =>
      expect(native.bridge.setVisible).toHaveBeenCalledWith(42, false),
    );

    await act(async () => {
      native.emit({ type: "escapePressed" });
    });
    expect(onRequestClose).toHaveBeenCalledOnce();
    unmount();
    expect(native.bridge.destroyView).toHaveBeenCalledWith(42);
  });

  it("oferece link global que seleciona a Browser Workbench", () => {
    render(
      <BrowserSettingsContent
        threadId="thread-1"
        workspaceId="workspace-1"
        browserProfileId="session-profile"
        presentation="settings"
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(setOpen).toHaveBeenCalledWith(false);
    expect(openBrowserSettings).toHaveBeenCalledWith("thread-1");
  });

  it("falha com segurança quando a navegação nativa não confirma o carregamento", async () => {
    vi.useFakeTimers();
    const native = installBridge();
    const { unmount } = render(
      <BrowserSettingsContent
        threadId="thread-timeout"
        workspaceId="workspace-1"
        browserProfileId="session-profile"
        presentation="workbench"
      />,
    );

    await act(async () => {
      await Promise.resolve();
      vi.advanceTimersByTime(8_000);
    });
    expect(screen.getByText("Could not open browser settings.")).toBeTruthy();
    expect(native.bridge.destroyView).toHaveBeenCalledWith(42);
    unmount();
    vi.useRealTimers();
  });
});
