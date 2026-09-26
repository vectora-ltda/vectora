/**
 * windows-store — janelas flutuantes da "workstation".
 *
 * Uma janela por workspace (id = workspaceId). Cada janela suporta múltiplas
 * abas (tabs). Abrir um arquivo já aberto na mesma janela apenas ativa a aba;
 * abrir um arquivo novo adiciona uma aba. Fechar a última aba fecha a janela.
 * Posição/tamanho/minimização persists por usuário em localStorage.
 */

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { useSettingsStore } from "./settings-store";
import type { EditedFile } from "@/lib/types";
import type { GitCommitDetailsState } from "@/components/workbench/git/history-view";
import type { PlanItem } from "./workbench-store";

export interface FileWindowState {
  /** workspaceId — uma janela por workspace */
  id: string;
  workspaceId: string;
  /** Caminhos de arquivo abertos como abas (na ordem de abertura). */
  tabs: string[];
  /** Aba atualmente visível. */
  activeTab: string;
  /** Basename da aba ativa, exibido na barra de título e no dock. */
  title: string;
  x: number;
  y: number;
  w: number;
  h: number;
  minimized: boolean;
  zIndex: number;
}

export type CanvasDocumentKind =
  "file" | "file-diff" | "commit-details" | "plan" | "mcp-preview";

export interface McpCanvasPreviewData {
  id: string;
  name: string;
  description: string;
  installCommand: string;
  envVars: string[];
  homepage: string;
  category: string;
  iconUrl?: string | null;
  publisher?: string | null;
  publisherUrl?: string | null;
  starsCount?: number;
  downloadsCount?: number;
  runtimeHint?: string | null;
  transport?: string;
}

/** Serializable document descriptor shared by IDE and assistant canvases. */
export interface CanvasDocumentDescriptor {
  id: string;
  kind: CanvasDocumentKind;
  workspaceId: string | null;
  threadId?: string;
  title: string;
  path?: string;
  commitSha?: string;
  editedFile?: EditedFile;
  commitDetails?: GitCommitDetailsState;
  plan?: { item: PlanItem; content: string | null };
  mcp?: McpCanvasPreviewData;
}

interface WindowsState {
  windows: FileWindowState[];
  /** Maior zIndex já atribuído (cresce ao focar). */
  topZ: number;

  /** Estado do editor docked (modo IDE). */
  dockedWorkspaceId: string | null;
  dockedTabs: string[];
  dockedActiveTab: string | null;
  canvasDocuments: CanvasDocumentDescriptor[];
  activeCanvasDocumentId: string | null;

  /** Abre path na janela do workspace. Cria a janela se não existir, ou
   * adiciona uma aba se a janela já existir. */
  open: (workspaceId: string, path: string) => void;
  /** Fecha a janela inteira (todas as abas). */
  close: (id: string) => void;
  /** Fecha todas as janelas (reset ao iniciar nova conversa / apagar sessão). */
  closeAll: () => void;
  /** Remove uma aba. Se for a última, fecha a janela. */
  closeTab: (id: string, path: string) => void;
  /** Ativa uma aba existente na janela. */
  setActiveTab: (id: string, path: string) => void;
  focus: (id: string) => void;
  minimize: (id: string) => void;
  restore: (id: string) => void;
  setBounds: (
    id: string,
    bounds: Partial<Pick<FileWindowState, "x" | "y" | "w" | "h">>,
  ) => void;

  /** Abre path no editor docked (modo IDE). Reseta tabs ao trocar workspace. */
  openDocked: (workspaceId: string, path: string) => void;
  setDockedActiveTab: (path: string) => void;
  /** Fecha uma tab docked; última tab → zera tudo. */
  closeDockedTab: (path: string) => void;
  openCanvasDocument: (document: CanvasDocumentDescriptor) => void;
  activateCanvasDocument: (id: string) => void;
  closeCanvasDocument: (id: string) => void;
  closeCanvasDocumentAndDockedTab: (id: string) => void;
  closeCanvasDocumentModal: (id: string) => void;
  clearCanvasDocumentsForWorkspace: (workspaceId: string) => void;
}

const BASE_Z = 100;

function basename(path: string): string {
  return path.split(/[/\\]/).pop() || path;
}

const DEFAULT_WIN_W = 640;
const DEFAULT_WIN_H = 460;

interface PersistedWindowsState {
  windows?: FileWindowState[];
  topZ?: number;
  canvasDocuments?: unknown;
  activeCanvasDocumentId?: string | null;
}

function sanitizePersistedCanvas(state: PersistedWindowsState) {
  const rawDocuments = Array.isArray(state.canvasDocuments)
    ? state.canvasDocuments
    : [];
  const documents = rawDocuments.filter(
    (document): document is CanvasDocumentDescriptor => {
      if (!document || typeof document !== "object") return false;
      const candidate = document as Partial<CanvasDocumentDescriptor>;
      if (
        typeof candidate.id !== "string" ||
        candidate.id.trim().length === 0 ||
        (candidate.workspaceId !== null &&
          (typeof candidate.workspaceId !== "string" ||
            candidate.workspaceId.trim().length === 0)) ||
        typeof candidate.title !== "string"
      ) {
        return false;
      }
      if (candidate.kind === "file" || candidate.kind === "file-diff") {
        return (
          typeof candidate.path === "string" && candidate.path.trim().length > 0
        );
      }
      return (
        candidate.kind === "commit-details" ||
        candidate.kind === "plan" ||
        candidate.kind === "mcp-preview"
      );
    },
  );
  const activeCanvasDocumentId = documents.some(
    (document) => document.id === state.activeCanvasDocumentId,
  )
    ? (state.activeCanvasDocumentId ?? null)
    : null;
  return { documents, activeCanvasDocumentId };
}

/** Posição inicial centralizada na área de conteúdo VISÍVEL (descontando a
 * sidebar esquerda), não no viewport inteiro. `count` escalona janelas
 * subsequentes a partir da mesma centralização. */
function initialBounds(count: number): { x: number; y: number } {
  if (typeof window === "undefined") {
    return { x: 80 + (count % 6) * 32, y: 80 + (count % 6) * 32 };
  }
  const sidebarWidth = useSettingsStore.getState().sidebarWidth;
  const contentLeft = sidebarWidth;
  const contentWidth = Math.max(
    window.innerWidth - sidebarWidth,
    DEFAULT_WIN_W,
  );
  const centerX = contentLeft + (contentWidth - DEFAULT_WIN_W) / 2;
  const centerY = (window.innerHeight - DEFAULT_WIN_H) / 2;
  const stagger = (count % 6) * 32;
  return {
    x: Math.max(contentLeft, centerX + stagger),
    y: Math.max(24, centerY + stagger),
  };
}

export const useWindowsStore = create<WindowsState>()(
  persist(
    (set, get) => ({
      windows: [],
      topZ: BASE_Z,
      dockedWorkspaceId: null,
      dockedTabs: [],
      dockedActiveTab: null,
      canvasDocuments: [],
      activeCanvasDocumentId: null,

      openCanvasDocument: (document) =>
        set((s) => {
          const existing = s.canvasDocuments.find(
            (item) => item.id === document.id,
          );
          const documents = existing
            ? s.canvasDocuments.map((item) =>
                item.id === document.id ? document : item,
              )
            : [...s.canvasDocuments, document];
          return {
            canvasDocuments: documents,
            activeCanvasDocumentId: document.id,
          };
        }),

      activateCanvasDocument: (id) =>
        set((s) =>
          s.canvasDocuments.some((document) => document.id === id)
            ? { activeCanvasDocumentId: id }
            : s,
        ),

      closeCanvasDocument: (id) =>
        set((s) => {
          const documents = s.canvasDocuments.filter(
            (document) => document.id !== id,
          );
          return {
            canvasDocuments: documents,
            activeCanvasDocumentId:
              s.activeCanvasDocumentId === id
                ? (documents.at(-1)?.id ?? null)
                : s.activeCanvasDocumentId,
          };
        }),

      closeCanvasDocumentAndDockedTab: (id) =>
        set((s) => {
          const document = s.canvasDocuments.find((item) => item.id === id);
          const closesDockedTab = Boolean(
            document?.kind === "file" &&
            document.path &&
            document.workspaceId === s.dockedWorkspaceId &&
            s.dockedTabs.includes(document.path),
          );
          const documents = s.canvasDocuments.filter((item) => item.id !== id);
          if (!closesDockedTab || !document?.path) {
            return {
              canvasDocuments: documents,
              activeCanvasDocumentId:
                s.activeCanvasDocumentId === id
                  ? (documents.at(-1)?.id ?? null)
                  : s.activeCanvasDocumentId,
            };
          }
          const tabs = s.dockedTabs.filter((path) => path !== document.path);
          const activeTab =
            s.dockedActiveTab === document.path
              ? (tabs[Math.max(0, s.dockedTabs.indexOf(document.path) - 1)] ??
                tabs[0] ??
                null)
              : s.dockedActiveTab;
          const activeCanvasDocumentId =
            s.activeCanvasDocumentId === id
              ? activeTab && s.dockedWorkspaceId
                ? `file:${s.dockedWorkspaceId}:${activeTab}`
                : (documents.at(-1)?.id ?? null)
              : s.activeCanvasDocumentId;
          return {
            dockedTabs: tabs,
            dockedActiveTab: activeTab,
            dockedWorkspaceId: tabs.length > 0 ? s.dockedWorkspaceId : null,
            canvasDocuments: documents,
            activeCanvasDocumentId,
          };
        }),

      closeCanvasDocumentModal: (id) =>
        set((s) => ({
          canvasDocuments: s.canvasDocuments.filter(
            (document) => document.id !== id,
          ),
          activeCanvasDocumentId:
            s.activeCanvasDocumentId === id ? null : s.activeCanvasDocumentId,
        })),

      clearCanvasDocumentsForWorkspace: (workspaceId) =>
        set((s) => {
          const documents = s.canvasDocuments.filter(
            (document) => document.workspaceId !== workspaceId,
          );
          return {
            canvasDocuments: documents,
            activeCanvasDocumentId: documents.some(
              (document) => document.id === s.activeCanvasDocumentId,
            )
              ? s.activeCanvasDocumentId
              : (documents.at(-1)?.id ?? null),
          };
        }),

      openDocked: (workspaceId, path) =>
        set((s) => {
          const document: CanvasDocumentDescriptor = {
            id: `file:${workspaceId}:${path}`,
            kind: "file",
            workspaceId,
            title: basename(path),
            path,
          };
          const canvasDocuments = [
            ...s.canvasDocuments.filter(
              (item) =>
                item.workspaceId !== workspaceId || item.id !== document.id,
            ),
            document,
          ];
          if (s.dockedWorkspaceId !== workspaceId) {
            return {
              dockedWorkspaceId: workspaceId,
              dockedTabs: [path],
              dockedActiveTab: path,
              canvasDocuments,
              activeCanvasDocumentId: document.id,
            };
          }
          const tabs = s.dockedTabs.includes(path)
            ? s.dockedTabs
            : [...s.dockedTabs, path];
          return {
            dockedTabs: tabs,
            dockedActiveTab: path,
            canvasDocuments,
            activeCanvasDocumentId: document.id,
          };
        }),

      setDockedActiveTab: (path) =>
        set((s) =>
          s.dockedTabs.includes(path)
            ? {
                dockedActiveTab: path,
                activeCanvasDocumentId: s.dockedWorkspaceId
                  ? `file:${s.dockedWorkspaceId}:${path}`
                  : s.activeCanvasDocumentId,
              }
            : s,
        ),

      closeDockedTab: (path) =>
        set((s) => {
          const tabs = s.dockedTabs.filter((t) => t !== path);
          if (tabs.length === 0) {
            return {
              dockedWorkspaceId: null,
              dockedTabs: [],
              dockedActiveTab: null,
              canvasDocuments: s.canvasDocuments.filter(
                (document) =>
                  document.workspaceId !== s.dockedWorkspaceId ||
                  document.kind !== "file",
              ),
              activeCanvasDocumentId:
                s.activeCanvasDocumentId ===
                `file:${s.dockedWorkspaceId ?? ""}:${path}`
                  ? null
                  : s.activeCanvasDocumentId,
            };
          }
          const activeTab =
            s.dockedActiveTab === path
              ? (tabs[Math.max(0, s.dockedTabs.indexOf(path) - 1)] ?? tabs[0])
              : s.dockedActiveTab;
          return {
            dockedTabs: tabs,
            dockedActiveTab: activeTab,
            canvasDocuments: s.canvasDocuments.filter(
              (document) =>
                document.id !== `file:${s.dockedWorkspaceId ?? ""}:${path}`,
            ),
            activeCanvasDocumentId:
              s.activeCanvasDocumentId ===
                `file:${s.dockedWorkspaceId ?? ""}:${path}` &&
              s.dockedWorkspaceId
                ? `file:${s.dockedWorkspaceId}:${activeTab}`
                : s.activeCanvasDocumentId,
          };
        }),

      open: (workspaceId, path) =>
        set((s) => {
          const id = workspaceId;
          const z = s.topZ + 1;
          const existing = s.windows.find((w) => w.id === id);

          if (existing) {
            const tabs = existing.tabs.includes(path)
              ? existing.tabs
              : [...existing.tabs, path];
            return {
              topZ: z,
              windows: s.windows.map((w) =>
                w.id === id
                  ? {
                      ...w,
                      tabs,
                      activeTab: path,
                      title: basename(path),
                      minimized: false,
                      zIndex: z,
                    }
                  : w,
              ),
            };
          }

          const count = s.windows.length;
          const { x, y } = initialBounds(count);
          const next: FileWindowState = {
            id,
            workspaceId,
            tabs: [path],
            activeTab: path,
            title: basename(path),
            x,
            y,
            w: DEFAULT_WIN_W,
            h: DEFAULT_WIN_H,
            minimized: false,
            zIndex: z,
          };
          return { topZ: z, windows: [...s.windows, next] };
        }),

      close: (id) =>
        set((s) => ({ windows: s.windows.filter((w) => w.id !== id) })),

      closeAll: () =>
        set({
          windows: [],
          dockedWorkspaceId: null,
          dockedTabs: [],
          dockedActiveTab: null,
          canvasDocuments: [],
          activeCanvasDocumentId: null,
        }),

      closeTab: (id, path) =>
        set((s) => {
          const win = s.windows.find((w) => w.id === id);
          if (!win) return s;
          const tabs = win.tabs.filter((t) => t !== path);
          if (tabs.length === 0) {
            return { windows: s.windows.filter((w) => w.id !== id) };
          }
          const activeTab =
            win.activeTab === path
              ? (tabs[Math.max(0, win.tabs.indexOf(path) - 1)] ?? tabs[0])
              : win.activeTab;
          return {
            windows: s.windows.map((w) =>
              w.id === id
                ? { ...w, tabs, activeTab, title: basename(activeTab) }
                : w,
            ),
          };
        }),

      setActiveTab: (id, path) =>
        set((s) => ({
          windows: s.windows.map((w) =>
            w.id === id && w.tabs.includes(path)
              ? { ...w, activeTab: path, title: basename(path) }
              : w,
          ),
        })),

      focus: (id) =>
        set((s) => {
          const w = s.windows.find((x) => x.id === id);
          if (!w || w.zIndex === s.topZ) return s;
          const z = s.topZ + 1;
          return {
            topZ: z,
            windows: s.windows.map((x) =>
              x.id === id ? { ...x, zIndex: z } : x,
            ),
          };
        }),

      minimize: (id) =>
        set((s) => ({
          windows: s.windows.map((w) =>
            w.id === id ? { ...w, minimized: true } : w,
          ),
        })),

      restore: (id) =>
        set((s) => {
          const z = s.topZ + 1;
          return {
            topZ: z,
            windows: s.windows.map((w) =>
              w.id === id ? { ...w, minimized: false, zIndex: z } : w,
            ),
          };
        }),

      setBounds: (id, bounds) =>
        set((s) => ({
          windows: s.windows.map((w) =>
            w.id === id ? { ...w, ...bounds } : w,
          ),
        })),
    }),
    {
      name: "vectora-windows",
      version: 1,
      migrate: (persistedState: unknown) => {
        const state = (persistedState ?? {}) as PersistedWindowsState;
        const canvas = sanitizePersistedCanvas(state);
        return {
          windows: Array.isArray(state.windows) ? state.windows : [],
          topZ: typeof state.topZ === "number" ? state.topZ : BASE_Z,
          canvasDocuments: canvas.documents,
          activeCanvasDocumentId: canvas.activeCanvasDocumentId,
        };
      },
      storage: createJSONStorage(() =>
        typeof window !== "undefined"
          ? localStorage
          : {
              getItem: () => null,
              setItem: () => {},
              removeItem: () => {},
            },
      ),
      // Estado do editor docked (modo IDE) é efêmero de sessão — persistir
      // sobrevivia a reload e vazava entre workspaces (abrir uma sessão
      // nova mostrava o último arquivo docado de OUTRO workspace, já que
      // dockedWorkspaceId/dockedTabs/dockedActiveTab ficavam numa chave
      // global de localStorage sem escopo por workspace).
      partialize: (state) => ({
        windows: state.windows,
        topZ: state.topZ,
        canvasDocuments: state.canvasDocuments,
        activeCanvasDocumentId: state.activeCanvasDocumentId,
      }),
    },
  ),
);
