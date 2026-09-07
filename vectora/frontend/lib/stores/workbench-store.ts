/**
 * workbench-store — estado do painel lateral multi-aba
 * (Terminal · Arquivos · Diff · Plano).
 *
 * Estrutura em duas camadas:
 *   1. **Shell persistido** (zustand/middleware/persist) — sobrevive reload:
 *      painel aberto/fechado, aba ativa, terminais (metadados), tamanho do
 *      split, pins. Chave `vectora-workbench-{user_id}`.
 *   2. **Caches voláteis** das abas Files/Diff/Plan — sobrevivem a remount
 *      e troca de aba (igual ao threads-store), mas não a reload. SWR pattern:
 *      render imediato do cache, refetch silencioso se stale.
 *
 * O xterm.js e o WebSocket continuam vivendo em refs dentro do componente;
 * o store guarda apenas metadados compartilhados.
 */

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { immer } from "zustand/middleware/immer";

import { getThreadPins, setThreadPins } from "@/lib/api/vectora-client";

/** Referência estável de lista vazia (evita criar novo [] a cada selector). */
const EMPTY_LIST: TerminalInstance[] = [];
/** Janela default para considerar uma entrada do cache "stale" (ms). */
export const WORKBENCH_STALE_MS = 30_000;

/** Default legado de `splitSize` (v0/v1),
 * substituído pelo padrão do VS Code. */
export const LEGACY_SPLIT_SIZE_DEFAULT = 224;
/** Default atual — casa com `sidebarWidth` do settings-store. */
export const SPLIT_SIZE_DEFAULT = 280;

/** Converte `splitSize` legado (%, v0) ou default antigo (px, v1) pro
 * default atual — só bumpa quem está exatamente no valor antigo, largura
 * escolhida manualmente pelo usuário não é sobrescrita. */
export function migrateSplitSize(
  splitSize: unknown,
  fromVersion: number,
): unknown {
  if (fromVersion < 1 && typeof splitSize === "number" && splitSize <= 100) {
    return LEGACY_SPLIT_SIZE_DEFAULT;
  }
  if (fromVersion < 2 && splitSize === LEGACY_SPLIT_SIZE_DEFAULT) {
    return SPLIT_SIZE_DEFAULT;
  }
  return splitSize;
}

// ---------------------------------------------------------------------------
// Tipos compartilhados
// ---------------------------------------------------------------------------

export interface TerminalInstance {
  /** ID do terminal no backend (pty_registry). */
  id: string;
  /** Título exibido na aba interna do terminal. */
  title: string;
  /** Workspace ao qual o terminal está atrelado. */
  workspaceId: string;
}

/** Abas disponíveis no painel lateral do workbench. */
export type WorkbenchTab =
  | "terminal"
  | "files"
  | "diff"
  | "plan"
  | "browser"
  | "storage"
  | "tasks"
  | "context_graph"
  | "library";

// Ordem: file system → git → plan → segundo plano → browser → memory (rag) →
// context graph → library (MCP/Skills/Memory) → terminal (shell).
export const WORKBENCH_TABS: WorkbenchTab[] = [
  "files",
  "diff",
  "plan",
  "tasks",
  "browser",
  "storage",
  "context_graph",
  "library",
  "terminal",
];

// ── Files cache ────────────────────────────────────────────────────────────

export interface FileEntry {
  name: string;
  path: string;
  kind: "dir" | "file";
  size?: number;
}

export interface FileContent {
  path: string;
  kind: "text" | "binary";
  content?: string;
  size: number;
  truncated?: boolean;
  /** sha256 do conteúdo — ausente quando truncado (edição fica desabilitada). */
  sha256?: string | null;
}

interface FilesCache {
  /** Diretórios atualmente expandidos (path relativo). */
  expandedDirs: string[];
  /** Entradas de cada diretório já carregado. */
  entriesByDir: Record<string, FileEntry[]>;
  /** Arquivo atualmente aberto no viewer (ou null). */
  openPath: string | null;
  /** Conteúdo de até 8 arquivos abertos recentemente (LRU implícito). */
  contents: Record<string, FileContent>;
  /** Filtro de busca (vive enquanto a aba viver). */
  filter: string;
  /** Timestamp da última carga de cada diretório. */
  fetchedAt: Record<string, number>;
}

// ── Diff cache ─────────────────────────────────────────────────────────────

export interface DiffFile {
  path: string;
  status: "M" | "A" | "D" | "R" | "?";
  additions: number;
  deletions: number;
  /** Flag staged (índice) — char do git status, ou null. Schema 2. */
  staged_change?: string | null;
  /** Flag unstaged (working tree) — char do git status, ou null. Schema 2. */
  unstaged_change?: string | null;
  /** Arquivo não rastreado. Schema 2. */
  untracked?: boolean;
}

export interface DiffHunk {
  header: string;
  lines: string[];
}

export interface DiffSummary {
  is_git_repo: boolean;
  total_additions: number;
  total_deletions: number;
  files: DiffFile[];
}

export interface GitOpsSnapshot {
  operationId: string;
  operation: string;
  state: "queued" | "running" | "succeeded" | "failed";
  phase: string;
  progress: number;
  errorCode: string | null;
  error: string | null;
  updatedAt: number;
}

export interface GitOpsState {
  selectedFiles: string[];
  selectedHunks: Record<string, number[]>;
  activeDocument: string | null;
  operation: GitOpsSnapshot | null;
}

interface DiffCache {
  summary: DiffSummary | null;
  openFiles: string[];
  hunksByFile: Record<string, DiffHunk[]>;
  summaryFetchedAt: number;
  fileFetchedAt: Record<string, number>;
}

// ── Plan cache ─────────────────────────────────────────────────────────────

export interface PlanItem {
  title: string;
  path: string;
  session_id: string;
  created_at: string;
  artifact_type?: string;
  content_preview?: string | null;
}

interface PlanCache {
  items: PlanItem[];
  /** Slugs abertos no Accordion — vários simultâneos (`type="multiple"`). */
  openSlugs: string[];
  contentsBySlug: Record<string, string>;
  fetchedAt: number;
}

// ── Todos (tool nativa write_todos) ──────────────────────────────────────────
// Checklist ao vivo do turno atual — distinto dos artifacts (`plan` acima):
// documentos salvos vs. progresso de execução em tempo real. Entregue via
// evento SSE dedicado (`todos_updated`), não persiste em localStorage — a
// fonte de verdade é o checkpoint da sessão.

export interface TodoItem {
  content: string;
  status: "pending" | "in_progress" | "completed";
}

// ── Tasks (background tasks/runs da sessão) ─────────────────────────────────
// Cache real (era `useState` local em `use-background-tasks.ts`, perdido a
// cada desmontagem — reabrir a aba Tarefas sempre refazia o fetch do zero,
// mesmo sem nada ter mudado). Tipos duplicados estruturalmente (não
// importados de `use-background-tasks.ts`) pra manter o store sem
// dependência do hook — TS estrutural cobre a compatibilidade.

export interface BackgroundTaskItem {
  id: string;
  session_id: string;
  workspace_id: string | null;
  kind: "routine" | "heartbreak" | "subagent";
  name: string;
  instruction: string;
  trigger_type: "interval" | "webhook" | "manual" | "subagent";
  trigger_config: Record<string, unknown>;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
}

export interface BackgroundRunItem {
  id: string;
  task_id: string;
  run_thread_id: string | null;
  trigger_source: string;
  status: "running" | "done" | "error" | "awaiting_approval" | "cancelled";
  summary: string | null;
  started_at: string;
  finished_at: string | null;
}

interface TasksCache {
  tasks: BackgroundTaskItem[];
  runs: BackgroundRunItem[];
  fetchedAt: number;
}

// ---------------------------------------------------------------------------
// Estado do store
// ---------------------------------------------------------------------------

interface WorkbenchState {
  // ── Shell persistido ──────────────────────────────────────────────────────
  byThread: Record<string, TerminalInstance[]>;
  activeByThread: Record<string, string | null>;
  panelOpen: Record<string, boolean>;
  activeTabByThread: Record<string, WorkbenchTab>;
  /** Largura do painel direito em px (default = largura da sidebar esquerda). */
  splitSize: number;
  /** Altura do viewer de arquivo (base do split vertical da aba Files) em px. */
  viewerHeight: number;
  /** Arquivos fixados por sessão. */
  pinnedFiles: Record<string, string[]>;

  list: (threadId: string) => TerminalInstance[];
  active: (threadId: string) => TerminalInstance | null;
  isOpen: (threadId: string) => boolean;

  open: (threadId: string, instance: TerminalInstance) => void;
  close: (threadId: string, id: string) => void;
  setActive: (threadId: string, id: string) => void;
  togglePanel: (threadId: string) => void;
  setPanelOpen: (threadId: string, open: boolean) => void;

  getActiveTab: (threadId: string) => WorkbenchTab;
  setActiveTab: (threadId: string, tab: WorkbenchTab) => void;
  /** Clique na nav-bar: troca para `tab` e abre; se `tab` já é a ativa e o
   * painel está aberto, fecha (colapsa) o painel. */
  selectTab: (threadId: string, tab: WorkbenchTab) => void;

  setSplitSize: (size: number) => void;
  setViewerHeight: (height: number) => void;

  togglePinned: (threadId: string, path: string) => void;
  isPinned: (threadId: string, path: string) => boolean;
  /** Substitui o cache local de pins com a lista vinda do backend (§8). */
  setPins: (threadId: string, pins: string[]) => void;
  /** Carrega os pins da sessão do backend (fonte de verdade). */
  loadPins: (threadId: string) => Promise<void>;

  // ── Caches voláteis ───────────────────────────────────────────────────────
  files: Record<string, FilesCache>;
  diff: Record<string, DiffCache>;
  plan: Record<string, PlanCache>;
  todos: Record<string, TodoItem[]>;
  gitOps: Record<string, GitOpsState>;

  // Files
  getFiles: (wsId: string) => FilesCache;
  setFilesEntries: (wsId: string, path: string, entries: FileEntry[]) => void;
  toggleExpanded: (wsId: string, path: string) => void;
  setOpenFile: (wsId: string, path: string | null) => void;
  setFileContent: (wsId: string, path: string, content: FileContent) => void;
  setFilesFilter: (wsId: string, filter: string) => void;
  invalidateFiles: (wsId?: string) => void;

  // Diff
  getDiff: (wsId: string) => DiffCache;
  setDiffSummary: (wsId: string, summary: DiffSummary) => void;
  setDiffOpenFile: (wsId: string, path: string, open: boolean) => void;
  setDiffHunks: (wsId: string, path: string, hunks: DiffHunk[]) => void;
  invalidateDiff: (wsId?: string) => void;

  // Plan
  getPlan: (threadId: string) => PlanCache;
  setPlanItems: (threadId: string, items: PlanItem[]) => void;
  /** Abre/fecha `slug` no Accordion sem afetar os demais já abertos. */
  togglePlanOpenSlug: (threadId: string, slug: string) => void;
  setPlanContent: (threadId: string, slug: string, content: string) => void;
  invalidatePlan: (threadId?: string) => void;

  // Todos
  getTodos: (threadId: string) => TodoItem[];
  setTodos: (threadId: string, todos: TodoItem[]) => void;

  getGitOps: (wsId: string) => GitOpsState;
  toggleGitFileSelection: (wsId: string, path: string) => void;
  setGitHunkSelection: (wsId: string, path: string, indexes: number[]) => void;
  setGitActiveDocument: (wsId: string, path: string | null) => void;
  setGitOperation: (wsId: string, operation: GitOpsSnapshot | null) => void;
  clearGitSelection: (wsId: string) => void;

  // Tasks (background tasks/runs)
  tasks: Record<string, TasksCache>;
  getTasks: (threadId: string) => TasksCache;
  setTasksData: (
    threadId: string,
    tasks: BackgroundTaskItem[],
    runs: BackgroundRunItem[],
  ) => void;
  invalidateTasks: (threadId?: string) => void;

  // Pendência de atualização por aba (volátil). Marcada quando uma tool do
  // agente edita o workspace e a aba Files/Diff não está montada; limpa
  // quando a aba é aberta e revalida.
  pending: Record<string, { files: boolean; diff: boolean }>;
  markPending: (wsId: string) => void;
  clearPending: (wsId: string, key: "files" | "diff") => void;
}

// Caches default usados pelos getters quando uma chave ainda não existe.
// Referências estáveis para não disparar re-renders em consumidores que
// fazem `s.getFiles(wsId)` sem haver entradas.
const EMPTY_FILES: FilesCache = {
  expandedDirs: [],
  entriesByDir: {},
  openPath: null,
  contents: {},
  filter: "",
  fetchedAt: {},
};
const EMPTY_DIFF: DiffCache = {
  summary: null,
  openFiles: [],
  hunksByFile: {},
  summaryFetchedAt: 0,
  fileFetchedAt: {},
};
const EMPTY_PLAN: PlanCache = {
  items: [],
  openSlugs: [],
  contentsBySlug: {},
  fetchedAt: 0,
};
const EMPTY_TODOS: TodoItem[] = [];
const EMPTY_GIT_OPS: GitOpsState = {
  selectedFiles: [],
  selectedHunks: {},
  activeDocument: null,
  operation: null,
};
const EMPTY_TASKS: TasksCache = { tasks: [], runs: [], fetchedAt: 0 };

// LRU simples: mantém só os últimos 8 conteúdos por workspace.
function pruneContents(
  contents: Record<string, FileContent>,
  keepLast = 8,
): Record<string, FileContent> {
  const keys = Object.keys(contents);
  if (keys.length <= keepLast) return contents;
  // Ordem de inserção (preservada por objetos modernos): mantém os últimos.
  const slice = keys.slice(-keepLast);
  const next: Record<string, FileContent> = {};
  for (const k of slice) next[k] = contents[k];
  return next;
}

export const useWorkbenchStore = create<WorkbenchState>()(
  immer(
    persist(
      (set, get) => ({
        // ── Shell defaults ──────────────────────────────────────────────────
        byThread: {},
        activeByThread: {},
        panelOpen: {},
        activeTabByThread: {},
        // Mesma largura default da sidebar esquerda (lib/stores/settings-store.ts).
        splitSize: SPLIT_SIZE_DEFAULT,
        viewerHeight: 280,
        pinnedFiles: {},
        pending: {},

        list: (threadId) => get().byThread[threadId] ?? EMPTY_LIST,
        active: (threadId) => {
          const list = get().byThread[threadId] ?? EMPTY_LIST;
          const id = get().activeByThread[threadId];
          return list.find((t) => t.id === id) ?? list[0] ?? null;
        },
        isOpen: (threadId) => Boolean(get().panelOpen[threadId]),

        open: (threadId, instance) =>
          set((s) => {
            const existing = s.byThread[threadId] ?? [];
            if (existing.some((t) => t.id === instance.id)) {
              return {
                activeByThread: {
                  ...s.activeByThread,
                  [threadId]: instance.id,
                },
                panelOpen: { ...s.panelOpen, [threadId]: true },
                activeTabByThread: {
                  ...s.activeTabByThread,
                  [threadId]: "terminal",
                },
              };
            }
            return {
              byThread: {
                ...s.byThread,
                [threadId]: [...existing, instance],
              },
              activeByThread: { ...s.activeByThread, [threadId]: instance.id },
              panelOpen: { ...s.panelOpen, [threadId]: true },
              activeTabByThread: {
                ...s.activeTabByThread,
                [threadId]: "terminal",
              },
            };
          }),

        close: (threadId, id) =>
          set((s) => {
            const filtered = (s.byThread[threadId] ?? []).filter(
              (t) => t.id !== id,
            );
            const wasActive = s.activeByThread[threadId] === id;
            return {
              byThread: { ...s.byThread, [threadId]: filtered },
              activeByThread: {
                ...s.activeByThread,
                [threadId]: wasActive
                  ? (filtered[0]?.id ?? null)
                  : s.activeByThread[threadId],
              },
            };
          }),

        setActive: (threadId, id) =>
          set((s) => ({
            activeByThread: { ...s.activeByThread, [threadId]: id },
          })),

        togglePanel: (threadId) =>
          set((s) => ({
            panelOpen: { ...s.panelOpen, [threadId]: !s.panelOpen[threadId] },
          })),

        setPanelOpen: (threadId, open) =>
          set((s) => ({ panelOpen: { ...s.panelOpen, [threadId]: open } })),

        getActiveTab: (threadId) => {
          const stored = get().activeTabByThread[threadId];
          if (stored) return stored;
          // Sem preferência salva: plano ativo vira o painel principal,
          // senão o default é Arquivos.
          const plan = get().plan[threadId];
          if (plan && plan.items.length > 0) return "plan";
          return "files";
        },
        setActiveTab: (threadId, tab) =>
          set((s) => ({
            activeTabByThread: { ...s.activeTabByThread, [threadId]: tab },
            panelOpen: { ...s.panelOpen, [threadId]: true },
          })),

        selectTab: (threadId, tab) =>
          set((s) => {
            const isOpen = Boolean(s.panelOpen[threadId]);
            const current = get().getActiveTab(threadId);
            if (isOpen && current === tab) {
              return { panelOpen: { ...s.panelOpen, [threadId]: false } };
            }
            return {
              activeTabByThread: { ...s.activeTabByThread, [threadId]: tab },
              panelOpen: { ...s.panelOpen, [threadId]: true },
            };
          }),

        setSplitSize: (size) => set({ splitSize: size }),
        setViewerHeight: (height) => set({ viewerHeight: height }),

        togglePinned: (threadId, path) => {
          const cur = get().pinnedFiles[threadId] ?? [];
          const next = cur.includes(path)
            ? cur.filter((p) => p !== path)
            : [...cur, path];
          // Otimista: atualiza o cache já; o backend é a fonte de verdade (§8)
          // e persiste a lista por sessão, injetando os pins no contexto do
          // agente. Falha de rede não trava a UI — reload reconcilia.
          set((s) => ({ pinnedFiles: { ...s.pinnedFiles, [threadId]: next } }));
          void setThreadPins(threadId, next)
            .then((res) => get().setPins(threadId, res.pins))
            .catch(() => {});
        },
        isPinned: (threadId, path) =>
          (get().pinnedFiles[threadId] ?? []).includes(path),
        setPins: (threadId, pins) =>
          set((s) => ({ pinnedFiles: { ...s.pinnedFiles, [threadId]: pins } })),
        loadPins: async (threadId) => {
          try {
            const res = await getThreadPins(threadId);
            get().setPins(threadId, res.pins);
          } catch {
            // backend indisponível — mantém o cache atual
          }
        },

        // ── Caches voláteis ─────────────────────────────────────────────────
        files: {},
        diff: {},
        plan: {},
        todos: {},
        gitOps: {},
        tasks: {},

        getFiles: (wsId) => get().files[wsId] ?? EMPTY_FILES,
        setFilesEntries: (wsId, path, entries) =>
          set((s) => {
            const cur = s.files[wsId] ?? EMPTY_FILES;
            return {
              files: {
                ...s.files,
                [wsId]: {
                  ...cur,
                  entriesByDir: { ...cur.entriesByDir, [path]: entries },
                  fetchedAt: { ...cur.fetchedAt, [path]: Date.now() },
                },
              },
            };
          }),
        toggleExpanded: (wsId, path) =>
          set((s) => {
            const cur = s.files[wsId] ?? EMPTY_FILES;
            const expanded = cur.expandedDirs.includes(path)
              ? cur.expandedDirs.filter((p) => p !== path)
              : [...cur.expandedDirs, path];
            return {
              files: { ...s.files, [wsId]: { ...cur, expandedDirs: expanded } },
            };
          }),
        setOpenFile: (wsId, path) =>
          set((s) => {
            const cur = s.files[wsId] ?? EMPTY_FILES;
            return {
              files: { ...s.files, [wsId]: { ...cur, openPath: path } },
            };
          }),
        setFileContent: (wsId, path, content) =>
          set((s) => {
            const cur = s.files[wsId] ?? EMPTY_FILES;
            const nextContents = pruneContents({
              ...cur.contents,
              [path]: content,
            });
            return {
              files: {
                ...s.files,
                [wsId]: { ...cur, contents: nextContents },
              },
            };
          }),
        setFilesFilter: (wsId, filter) =>
          set((s) => {
            const cur = s.files[wsId] ?? EMPTY_FILES;
            return { files: { ...s.files, [wsId]: { ...cur, filter } } };
          }),
        invalidateFiles: (wsId) =>
          set((s) => {
            if (!wsId) return { files: {} };
            const cur = s.files[wsId];
            if (!cur) return s;
            return {
              files: { ...s.files, [wsId]: { ...cur, fetchedAt: {} } },
            };
          }),

        getDiff: (wsId) => get().diff[wsId] ?? EMPTY_DIFF,
        setDiffSummary: (wsId, summary) =>
          set((s) => {
            const cur = s.diff[wsId] ?? EMPTY_DIFF;
            return {
              diff: {
                ...s.diff,
                [wsId]: {
                  ...cur,
                  summary,
                  summaryFetchedAt: Date.now(),
                },
              },
            };
          }),
        setDiffOpenFile: (wsId, path, open) =>
          set((s) => {
            const cur = s.diff[wsId] ?? EMPTY_DIFF;
            const next = open
              ? [...new Set([...cur.openFiles, path])]
              : cur.openFiles.filter((p) => p !== path);
            return { diff: { ...s.diff, [wsId]: { ...cur, openFiles: next } } };
          }),
        setDiffHunks: (wsId, path, hunks) =>
          set((s) => {
            const cur = s.diff[wsId] ?? EMPTY_DIFF;
            return {
              diff: {
                ...s.diff,
                [wsId]: {
                  ...cur,
                  hunksByFile: { ...cur.hunksByFile, [path]: hunks },
                  fileFetchedAt: { ...cur.fileFetchedAt, [path]: Date.now() },
                },
              },
            };
          }),
        invalidateDiff: (wsId) =>
          set((s) => {
            if (!wsId) return { diff: {} };
            const cur = s.diff[wsId];
            if (!cur) return s;
            return {
              diff: {
                ...s.diff,
                [wsId]: { ...cur, summaryFetchedAt: 0, fileFetchedAt: {} },
              },
            };
          }),

        getPlan: (threadId) => get().plan[threadId] ?? EMPTY_PLAN,
        setPlanItems: (threadId, items) =>
          set((s) => {
            const cur = s.plan[threadId] ?? EMPTY_PLAN;
            return {
              plan: {
                ...s.plan,
                [threadId]: { ...cur, items, fetchedAt: Date.now() },
              },
            };
          }),
        togglePlanOpenSlug: (threadId, slug) =>
          set((s) => {
            const cur = s.plan[threadId] ?? EMPTY_PLAN;
            const isOpen = cur.openSlugs.includes(slug);
            const openSlugs = isOpen
              ? cur.openSlugs.filter((s2) => s2 !== slug)
              : [...cur.openSlugs, slug];
            return {
              plan: { ...s.plan, [threadId]: { ...cur, openSlugs } },
            };
          }),
        setPlanContent: (threadId, slug, content) =>
          set((s) => {
            const cur = s.plan[threadId] ?? EMPTY_PLAN;
            return {
              plan: {
                ...s.plan,
                [threadId]: {
                  ...cur,
                  contentsBySlug: { ...cur.contentsBySlug, [slug]: content },
                },
              },
            };
          }),
        invalidatePlan: (threadId) =>
          set((s) => {
            if (!threadId) return { plan: {} };
            const cur = s.plan[threadId];
            if (!cur) return s;
            return {
              plan: { ...s.plan, [threadId]: { ...cur, fetchedAt: 0 } },
            };
          }),

        getTodos: (threadId) => get().todos[threadId] ?? EMPTY_TODOS,
        setTodos: (threadId, todos) =>
          set((s) => ({ todos: { ...s.todos, [threadId]: todos } })),

        getGitOps: (wsId) => get().gitOps[wsId] ?? EMPTY_GIT_OPS,
        toggleGitFileSelection: (wsId, path) =>
          set((s) => {
            const current = s.gitOps[wsId] ?? EMPTY_GIT_OPS;
            const selectedFiles = current.selectedFiles.includes(path)
              ? current.selectedFiles.filter((item) => item !== path)
              : [...current.selectedFiles, path];
            return {
              gitOps: { ...s.gitOps, [wsId]: { ...current, selectedFiles } },
            };
          }),
        setGitHunkSelection: (wsId, path, indexes) =>
          set((s) => {
            const current = s.gitOps[wsId] ?? EMPTY_GIT_OPS;
            return {
              gitOps: {
                ...s.gitOps,
                [wsId]: {
                  ...current,
                  selectedHunks: { ...current.selectedHunks, [path]: indexes },
                },
              },
            };
          }),
        setGitActiveDocument: (wsId, path) =>
          set((s) => {
            const current = s.gitOps[wsId] ?? EMPTY_GIT_OPS;
            return {
              gitOps: {
                ...s.gitOps,
                [wsId]: { ...current, activeDocument: path },
              },
            };
          }),
        setGitOperation: (wsId, operation) =>
          set((s) => {
            const current = s.gitOps[wsId] ?? EMPTY_GIT_OPS;
            return {
              gitOps: { ...s.gitOps, [wsId]: { ...current, operation } },
            };
          }),
        clearGitSelection: (wsId) =>
          set((s) => {
            const current = s.gitOps[wsId] ?? EMPTY_GIT_OPS;
            return {
              gitOps: {
                ...s.gitOps,
                [wsId]: { ...current, selectedFiles: [], selectedHunks: {} },
              },
            };
          }),

        getTasks: (threadId) => get().tasks[threadId] ?? EMPTY_TASKS,
        setTasksData: (threadId, tasks, runs) =>
          set((s) => ({
            tasks: {
              ...s.tasks,
              [threadId]: { tasks, runs, fetchedAt: Date.now() },
            },
          })),
        invalidateTasks: (threadId) =>
          set((s) => {
            if (!threadId) return { tasks: {} };
            const cur = s.tasks[threadId];
            if (!cur) return s;
            return {
              tasks: { ...s.tasks, [threadId]: { ...cur, fetchedAt: 0 } },
            };
          }),

        markPending: (wsId) =>
          set((s) => ({
            pending: { ...s.pending, [wsId]: { files: true, diff: true } },
          })),
        clearPending: (wsId, key) =>
          set((s) => {
            const cur = s.pending[wsId];
            if (!cur || !cur[key]) return s;
            return {
              pending: { ...s.pending, [wsId]: { ...cur, [key]: false } },
            };
          }),
      }),
      {
        name: "vectora-workbench",
        version: 2,
        // v0 guardava splitSize como % (default 40); v1 passa a usar px
        // (default = largura da sidebar). Valores antigos ficariam
        // minúsculos demais como largura — descarta e usa o novo default.
        // v2: default 224→280 (casa com sidebarWidth do settings-store) —
        // só bumpa quem está exatamente no default antigo, largura
        // escolhida manualmente não é sobrescrita.
        migrate: (persisted, version) => {
          const state = persisted as Partial<WorkbenchState>;
          if (!state || typeof state.splitSize === "undefined") return state;
          return {
            ...state,
            splitSize: migrateSplitSize(state.splitSize, version),
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
        // Apenas o "shell" persiste. Caches voláteis (files/diff/plan) ficam
        // de fora — são revalidados rápido e a verdade vive no backend.
        // `pinnedFiles` NÃO persiste: o backend é a fonte de verdade (§8) e
        // `loadPins` reconcilia o cache ao abrir a sessão.
        partialize: (state) => ({
          byThread: state.byThread,
          activeByThread: state.activeByThread,
          panelOpen: state.panelOpen,
          activeTabByThread: state.activeTabByThread,
          splitSize: state.splitSize,
          viewerHeight: state.viewerHeight,
        }),
      },
    ),
  ),
);

// ── Retro-compat (T3) ─────────────────────────────────────────────────────
// Mantém o nome legado para componentes que ainda não migraram.
export const useTerminalsStore = useWorkbenchStore;
