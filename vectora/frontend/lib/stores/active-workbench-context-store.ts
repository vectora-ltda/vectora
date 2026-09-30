import { create } from "zustand";

interface ActiveWorkbenchContextState {
  threadId: string | null;
  workspaceId: string | null;
  browserProfileId: string | null;
  setContext: (context: {
    threadId: string | null;
    workspaceId: string | null;
    browserProfileId?: string | null;
  }) => void;
  clear: () => void;
}

/** Contexto transitório da rota atual, sem persistência entre sessões. */
export const useActiveWorkbenchContextStore =
  create<ActiveWorkbenchContextState>((set) => ({
    threadId: null,
    workspaceId: null,
    browserProfileId: null,
    setContext: (context) => set(context),
    clear: () =>
      set({ threadId: null, workspaceId: null, browserProfileId: null }),
  }));
