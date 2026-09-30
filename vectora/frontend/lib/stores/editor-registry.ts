import { create } from "zustand";
import type { FileContent } from "@/lib/stores/workbench-store";

export interface EditorEntry {
  workspaceId: string;
  path: string;
  dirty: boolean;
  save: () => Promise<boolean>;
  saveAs: (path: string) => Promise<boolean>;
}

export interface EditorBuffer {
  file: FileContent;
  value: string;
  sha256: string | null;
}

/** Mantém edições locais quando uma aba deixa de ser a aba ativa. */
export const editorBuffers = new Map<string, EditorBuffer>();

interface EditorRegistryState {
  entries: Record<string, EditorEntry>;
  register: (key: string, entry: EditorEntry) => void;
  unregister: (key: string) => void;
  setDirty: (key: string, dirty: boolean) => void;
}

export function editorKey(workspaceId: string, path: string): string {
  return `${workspaceId}:${path}`;
}

export const useEditorRegistry = create<EditorRegistryState>((set) => ({
  entries: {},
  register: (key, entry) =>
    set((state) => ({ entries: { ...state.entries, [key]: entry } })),
  unregister: (key) =>
    set((state) => {
      const entries = { ...state.entries };
      delete entries[key];
      return { entries };
    }),
  setDirty: (key, dirty) =>
    set((state) =>
      state.entries[key]
        ? {
            entries: {
              ...state.entries,
              [key]: { ...state.entries[key], dirty },
            },
          }
        : state,
    ),
}));
