import { create } from "zustand";

interface PendingBrowserSettingsOpen {
  threadId: string;
  workspaceId: string | null;
}

interface BrowserSettingsControllerState {
  pendingOpen: PendingBrowserSettingsOpen | null;
  requestOpenNativeSettings: (request: PendingBrowserSettingsOpen) => void;
  consumePendingOpen: (threadId: string) => PendingBrowserSettingsOpen | null;
  clear: () => void;
}

/** One-shot bridge from global Settings to the Browser workbench. */
export const useBrowserSettingsController =
  create<BrowserSettingsControllerState>((set, get) => ({
    pendingOpen: null,
    requestOpenNativeSettings: (request) => set({ pendingOpen: request }),
    consumePendingOpen: (threadId) => {
      const pending = get().pendingOpen;
      if (!pending || pending.threadId !== threadId) return null;
      set({ pendingOpen: null });
      return pending;
    },
    clear: () => set({ pendingOpen: null }),
  }));
