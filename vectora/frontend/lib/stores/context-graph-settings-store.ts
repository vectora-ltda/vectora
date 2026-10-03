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

export const CONTEXT_GRAPH_PANEL_MIN_WIDTH = 180;
export const CONTEXT_GRAPH_PANEL_DEFAULT_WIDTH = 280;
export const CONTEXT_GRAPH_PANEL_MAX_WIDTH = 480;

interface ContextGraphSettingsState {
  /** Tipos a indexar. Vazio = todos (default). */
  fileTypes: GraphFileType[];
  /** "semantic" (AST + LLM) ou "ast" (só estrutura, sem LLM). */
  mode: GraphMode;
  /** Largura do painel de comunidades, indexada por workspace. */
  communityPanelWidths: Record<string, number>;
  toggleFileType: (t: GraphFileType) => void;
  setMode: (m: GraphMode) => void;
  setCommunityPanelWidth: (workspaceId: string, width: number) => void;
}

export const useContextGraphSettingsStore = create<ContextGraphSettingsState>()(
  persist(
    (set) => ({
      // Default explícito = todos os tipos (o usuário desmarca o que não quer).
      fileTypes: [...ALL_GRAPH_FILE_TYPES],
      mode: "semantic",
      communityPanelWidths: {},
      toggleFileType: (t) =>
        set((s) => ({
          fileTypes: s.fileTypes.includes(t)
            ? s.fileTypes.filter((x) => x !== t)
            : [...s.fileTypes, t],
        })),
      setMode: (m) => set({ mode: m }),
      setCommunityPanelWidth: (workspaceId, width) =>
        set((state) => ({
          communityPanelWidths: {
            ...state.communityPanelWidths,
            [workspaceId]: Math.max(
              CONTEXT_GRAPH_PANEL_MIN_WIDTH,
              Math.min(CONTEXT_GRAPH_PANEL_MAX_WIDTH, Math.round(width)),
            ),
          },
        })),
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
