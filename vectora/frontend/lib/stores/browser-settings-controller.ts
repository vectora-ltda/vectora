import { create } from "zustand";

interface BrowserSettingsControllerState {
  pendingOpen: { threadId: string } | null;
  requestOpenNativeSettings: (threadId: string) => void;
  consumePendingOpen: (threadId: string) => boolean;
}

/** Transient bridge between global Settings and the Browser workbench. */
export const useBrowserSettingsController =
  create<BrowserSettingsControllerState>((set, get) => ({
    pendingOpen: null,
    requestOpenNativeSettings: (threadId) => set({ pendingOpen: { threadId } }),
    consumePendingOpen: (threadId) => {
      const pending = get().pendingOpen;
      if (!pending || pending.threadId !== threadId) return false;
      set({ pendingOpen: null });
      return true;
    },
  }));
