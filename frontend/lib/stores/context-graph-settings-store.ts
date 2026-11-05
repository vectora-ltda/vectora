import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

/**
 * Settings do Context Graph (persistido por usuário em localStorage).
 *
 * Permite escolher QUAIS tipos de arquivo o grafo indexa e o modo de extração.
 * Caso de uso: indexar só `document` (markdown) para usar o Context Graph como
 * um "Obsidian" do workspace, deixando o código para o RAG vetorial.
 */

export type GraphFileType = "code" | "document" | "paper";

export const ALL_GRAPH_FILE_TYPES: GraphFileType[] = [
  "code",
  "document",
  "paper",
];

export type GraphMode = "semantic" | "ast";

interface ContextGraphSettingsState {
  /** Tipos a indexar. Vazio = todos (default). */
  fileTypes: GraphFileType[];
  /** "semantic" (AST + LLM) ou "ast" (só estrutura, sem LLM). */
  mode: GraphMode;
  toggleFileType: (t: GraphFileType) => void;
  setMode: (m: GraphMode) => void;
}

export const useContextGraphSettingsStore = create<ContextGraphSettingsState>()(
  persist(
    (set) => ({
      // Default explícito = todos os tipos (o usuário desmarca o que não quer).
      fileTypes: [...ALL_GRAPH_FILE_TYPES],
      mode: "semantic",
      toggleFileType: (t) =>
        set((s) => ({
          fileTypes: s.fileTypes.includes(t)
            ? s.fileTypes.filter((x) => x !== t)
            : [...s.fileTypes, t],
        })),
      setMode: (m) => set({ mode: m }),
    }),
    {
      name: "vectora-context-graph-settings",
      storage: createJSONStorage(() =>
        typeof window !== "undefined"
          ? localStorage
          : {
              getItem: () => null,
              setItem: () => {},
              removeItem: () => {},
            },
      ),
    },
  ),
);
