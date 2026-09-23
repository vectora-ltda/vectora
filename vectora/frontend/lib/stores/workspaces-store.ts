/**
 * Workspaces Store — Zustand
 *
 * Cache client-side da lista de workspaces e do workspace ativo.
 * Padrão stale-while-revalidate: exibe cache imediatamente e revalida
 * em background. Ações async conversam com o proxy Hono em /workspaces.
 *
 * - `status`/`error` substituem `loading: boolean` (máquina `AsyncStatus`);
 *   `hasLoaded(fetchedAt)` indica se já existe cache renderizável — refresh
 *   em background não derruba esse cache para um estado "carregando".
 * - `pending` rastreia operações individuais (hydrate/create/trust/gitInit)
 *   para que a UI desabilite só o botão certo, não a tela inteira.
 * - Falhas de rede/servidor em ações do usuário viram toast (canal único de
 *   feedback); nenhuma ação retorna silenciosamente `null`.
 * - Persistência via `localStorage`, mas só de `active_id`: a lista de
 *   workspaces é sempre revalidada do backend (source of truth) e nunca cruza
 *   contas diferentes no mesmo navegador.
 */

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import { useToastStore } from "./toast-store";
import { fetchJsonWithRetry, FetchHttpError } from "@/lib/utils/fetch-retry";
import {
  asyncError,
  asyncLoading,
  asyncSuccess,
  hasLoaded as computeHasLoaded,
  toErrorMessage,
  type AsyncStatus,
  type ActionResult,
} from "@/lib/types/async-state";
import { m } from "@/lib/paraglide/messages";
import { disposeBrowserWorkspace } from "@/lib/browser-session-store";

export type WorkspaceTransport = "local" | "ssh" | "codespace";

export interface WorkspaceInfo {
  id: string;
  name: string;
  cwd: string;
  /** true quando o usuário confiou na pasta — libera write/terminal/git */
  trusted: boolean;
  /** true se o cwd contém um repositório git */
  is_git_repo: boolean;
  git_remote: string | null;
  git_current_branch: string | null;
  git_default_branch: string | null;
  /** Espelha src/types/workspace.py */
  transport?: WorkspaceTransport;
  remote_host?: string | null;
  remote_path?: string | null;
  codespace_name?: string | null;
}

export interface DirEntry {
  name: string;
  path: string;
  is_dir: boolean;
  /** `"dir"` (default) ou `"drive"` para volumes do sistema (C:, /, /Volumes/...). */
  kind?: "dir" | "drive";
  /** Label do volume quando `kind === "drive"` (vazio quando ausente). */
  label?: string;
}

export interface BrowseResult {
  path: string;
  parent: string | null;
  entries: DirEntry[];
  /** ID da safe-root que cobre o path atual; null quando privilegiado
   *  navegando livre. Espelha o backend. */
  safe_root_id?: string | null;
  /** `true` quando `entries` lista volumes em vez de subdiretórios. */
  at_drives_root?: boolean;
  /** Caminho absoluto da pasta criada pela operação mkdir, quando aplicável. */
  created_path?: string | null;
}

/** Pseudo-path que dispara o modo "lista de discos" no backend. */
export const DRIVES_PSEUDO_PATH = "__drives__";

/** Resumo de uma safe-root para o painel de acesso rápido. */
export interface SafeRootSummary {
  id: string;
  path: string;
  label: string;
  builtin: boolean;
}

/** Codespace retornado por `gh codespace list`. */
export interface CodespaceSummary {
  name: string;
  repository: string;
  state: string;
  git_status?: Record<string, unknown> | null;
}

/** Operações individuais cujo progresso a UI precisa refletir. */
export interface WorkspacesPending {
  hydrate: boolean;
  create: boolean;
  trust: boolean;
  gitInit: boolean;
}

const PENDING_IDLE: WorkspacesPending = {
  hydrate: false,
  create: false,
  trust: false,
  gitInit: false,
};

let accountGeneration = 0;
let latestHydrateRequest = 0;
let latestSafeRootsRequest = 0;

interface WorkspacesState {
  workspaces: WorkspaceInfo[];
  active_id: string | null;
  fetchedAt: number | null;
  /** Máquina de estado da última revalidação (substitui `loading: boolean`). */
  status: AsyncStatus;
  /** Mensagem da última falha — `null` enquanto não houver erro. */
  error: string | null;
  /** Progresso por operação — habilita feedback granular. */
  pending: WorkspacesPending;
  safeRoots: SafeRootSummary[];

  // ── Reads ─────────────────────────────────────────────────────────────────
  getActive: () => WorkspaceInfo | null;
  getById: (id: string) => WorkspaceInfo | null;
  /** `true` quando já existe cache renderizável (ainda que stale). */
  hasLoaded: () => boolean;

  // ── Local writes ────────────────────────────────────────────────────────────
  setWorkspaces: (list: WorkspaceInfo[], activeId: string | null) => void;
  invalidate: () => void;
  /** Invalida respostas pendentes e remove dados da conta anterior. */
  resetForUser: () => void;

  // ── Async (proxy Hono) ──────────────────────────────────────────────────────
  hydrate: () => Promise<void>;
  setActive: (id: string) => Promise<void>;
  /** Só atualiza o estado local (sem POST /workspaces/set-active) — usado
   * quando o backend já persistiu a escolha sozinho (ex.: workspace criado
   * via ChatConfig.create_new_workspace, sincronizado de volta pelo
   * ThreadEvent.workspace_id). Dispara hydrate() em background pra o
   * workspace novo aparecer na lista sem bloquear o caller. */
  syncActiveLocal: (id: string) => void;
  create: (
    path: string,
    opts?: { trust?: boolean; git_init?: boolean },
  ) => Promise<ActionResult<WorkspaceInfo>>;
  trust: (id: string) => Promise<ActionResult<WorkspaceInfo>>;
  gitInit: (id: string) => Promise<ActionResult<WorkspaceInfo>>;
  browse: (path?: string) => Promise<BrowseResult | null>;
  /** Carrega safe-roots visíveis para o usuário atual. */
  loadSafeRoots: () => Promise<void>;

  // Workspaces remotos
  listSshKeys: () => Promise<string[]>;
  uploadSshKey: (file: File) => Promise<string | null>;
  deleteSshKey: (keyId: string) => Promise<boolean>;
  testSsh: (
    host: string,
    keyId?: string | null,
  ) => Promise<{ ok: boolean; message: string }>;
  listCodespaces: () => Promise<{
    codespaces: CodespaceSummary[];
    available: boolean;
    message: string;
  }>;
  createRemote: (body: {
    transport: "ssh" | "codespace";
    name?: string;
    remote_host?: string;
    remote_path?: string;
    ssh_key_id?: string | null;
    codespace_name?: string;
  }) => Promise<WorkspaceInfo | null>;
}

/**
 * Wrapper tolerante a falha (`| null`) usado pelas leituras auxiliares do
 * store (browse, safe-roots, ssh-keys, codespaces, set-active…).
 *
 * Para `GET`s (leituras idempotentes) delega a `fetchJsonWithRetry`
 * (retry exponencial em 5xx/queda de rede, sem retentar 4xx). Mutações
 * (`POST`/`PUT`/…) seguem sem retry — repetir poderia duplicar o efeito.
 */
async function fetchJson(url: string, init?: RequestInit): Promise<any | null> {
  const isRead = !init?.method || init.method.toUpperCase() === "GET";
  try {
    if (isRead) return await fetchJsonWithRetry(url, init);
    const res = await fetch(url, init);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/** Extrai uma mensagem de erro de uma resposta `{status, message?}` ou HTTP cru. */
async function readErrorMessage(res: Response): Promise<string> {
  const data = await res.json().catch(() => null);
  if (data && typeof data.message === "string" && data.message)
    return data.message;
  return `HTTP ${res.status}`;
}

/**
 * Equivalente a `readErrorMessage`, mas para erros já capturados de
 * `fetchJsonWithRetry` — o corpo chega como texto bruto em `err.message`.
 */
function httpErrorMessage(err: unknown): string | null {
  if (!(err instanceof FetchHttpError)) return null;
  try {
    const data: unknown = JSON.parse(err.message);
    if (
      data &&
      typeof data === "object" &&
      "message" in data &&
      typeof (data as { message?: unknown }).message === "string" &&
      (data as { message: string }).message
    ) {
      return (data as { message: string }).message;
    }
  } catch {
    // Corpo não é JSON — usa o fallback `HTTP {status}` do toErrorMessage.
  }
  return `HTTP ${err.status}`;
}

function setPending(
  set: (fn: (s: WorkspacesState) => Partial<WorkspacesState>) => void,
  key: keyof WorkspacesPending,
  value: boolean,
) {
  set((s) => ({ pending: { ...s.pending, [key]: value } }));
}

export const useWorkspacesStore = create<WorkspacesState>()(
  persist(
    (set, get) => ({
      workspaces: [],
      active_id: null,
      fetchedAt: null,
      status: "idle",
      error: null,
      pending: PENDING_IDLE,
      safeRoots: [],

      getActive: () => {
        const { workspaces, active_id } = get();
        if (!active_id) return workspaces[0] ?? null;
        return (
          workspaces.find((w) => w.id === active_id) ?? workspaces[0] ?? null
        );
      },

      getById: (id) => get().workspaces.find((w) => w.id === id) ?? null,

      hasLoaded: () => computeHasLoaded(get().fetchedAt),

      setWorkspaces: (list, activeId) =>
        set((state) => {
          const nextIds = new Set(list.map((workspace) => workspace.id));
          for (const workspace of state.workspaces) {
            if (!nextIds.has(workspace.id)) {
              disposeBrowserWorkspace(workspace.id);
            }
          }
          return {
            workspaces: list,
            active_id: activeId,
            fetchedAt: Date.now(),
            ...asyncSuccess(),
          };
        }),

      invalidate: () => set({ fetchedAt: null }),

      resetForUser: () => {
        accountGeneration += 1;
        latestHydrateRequest += 1;
        latestSafeRootsRequest += 1;
        for (const workspace of get().workspaces) {
          disposeBrowserWorkspace(workspace.id);
        }
        set({
          workspaces: [],
          active_id: null,
          safeRoots: [],
          fetchedAt: null,
          status: "idle",
          error: null,
        });
      },

      hydrate: async () => {
        const generation = accountGeneration;
        const requestId = ++latestHydrateRequest;
        set((s) => ({
          ...asyncLoading(),
          pending: { ...s.pending, hydrate: true },
        }));
        try {
          // Leitura idempotente: retenta em 5xx/queda de rede
          // (até 2x, backoff exponencial) antes de admitir falha ao usuário.
          const data = await fetchJsonWithRetry<{
            workspaces: WorkspaceInfo[];
            active_id?: string | null;
          }>("/workspaces");
          if (!data?.workspaces) {
            throw new Error("Resposta inesperada do servidor.");
          }
          if (
            generation !== accountGeneration ||
            requestId !== latestHydrateRequest
          )
            return;
          set((s) => {
            const nextIds = new Set(
              data.workspaces.map((workspace) => workspace.id),
            );
            for (const workspace of s.workspaces) {
              if (!nextIds.has(workspace.id)) {
                disposeBrowserWorkspace(workspace.id);
              }
            }
            return {
              workspaces: data.workspaces,
              active_id: s.active_id ?? data.active_id ?? null,
              fetchedAt: Date.now(),
              ...asyncSuccess(),
              pending: { ...s.pending, hydrate: false },
            };
          });
        } catch (err) {
          if (
            generation !== accountGeneration ||
            requestId !== latestHydrateRequest
          )
            return;
          const message = httpErrorMessage(err) ?? toErrorMessage(err);
          set((s) => ({
            ...asyncError(message),
            pending: { ...s.pending, hydrate: false },
          }));
          useToastStore.getState().error(m.workspaces_error_hydrate(), {
            description: message,
          });
        }
      },

      setActive: async (id) => {
        set({ active_id: id });
        await fetchJson("/workspaces/set-active", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ workspace_id: id }),
        });
      },

      syncActiveLocal: (id) => {
        latestHydrateRequest += 1;
        set({ active_id: id });
        void get().hydrate();
      },

      create: async (path, opts) => {
        setPending(set, "create", true);
        try {
          const res = await fetch("/workspaces/create", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              path,
              trust: opts?.trust ?? false,
              git_init: opts?.git_init ?? false,
            }),
          });
          if (!res.ok) throw new Error(await readErrorMessage(res));
          const data = await res.json();
          if (data?.status !== "ok" || !data.workspace) {
            throw new Error(
              typeof data?.message === "string"
                ? data.message
                : "Resposta inesperada do servidor.",
            );
          }
          await get().hydrate();
          set({ active_id: data.workspace.id });
          return { ok: true, data: data.workspace as WorkspaceInfo };
        } catch (err) {
          const message = toErrorMessage(err);
          useToastStore.getState().error(m.workspaces_error_create(), {
            description: message,
          });
          return { ok: false, error: message };
        } finally {
          setPending(set, "create", false);
        }
      },

      trust: async (id) => {
        setPending(set, "trust", true);
        try {
          const res = await fetch("/workspaces/trust", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ workspace_id: id }),
          });
          if (!res.ok) throw new Error(await readErrorMessage(res));
          const data = await res.json();
          if (data?.status !== "ok" || !data.workspace) {
            throw new Error(
              typeof data?.message === "string"
                ? data.message
                : "Resposta inesperada do servidor.",
            );
          }
          set((s) => ({
            workspaces: s.workspaces.map((w) =>
              w.id === id ? data.workspace : w,
            ),
          }));
          return { ok: true, data: data.workspace as WorkspaceInfo };
        } catch (err) {
          const message = toErrorMessage(err);
          useToastStore.getState().error(m.workspaces_error_trust(), {
            description: message,
          });
          return { ok: false, error: message };
        } finally {
          setPending(set, "trust", false);
        }
      },

      gitInit: async (id) => {
        setPending(set, "gitInit", true);
        try {
          const res = await fetch("/workspaces/git-init", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ workspace_id: id }),
          });
          if (!res.ok) throw new Error(await readErrorMessage(res));
          const data = await res.json();
          if (data?.status !== "ok" || !data.workspace) {
            throw new Error(
              typeof data?.message === "string"
                ? data.message
                : "Resposta inesperada do servidor.",
            );
          }
          set((s) => ({
            workspaces: s.workspaces.map((w) =>
              w.id === id ? data.workspace : w,
            ),
          }));
          return { ok: true, data: data.workspace as WorkspaceInfo };
        } catch (err) {
          const message = toErrorMessage(err);
          useToastStore.getState().error(m.workspaces_error_git_init(), {
            description: message,
          });
          return { ok: false, error: message };
        } finally {
          setPending(set, "gitInit", false);
        }
      },

      browse: async (path) => {
        const q = path ? `?path=${encodeURIComponent(path)}` : "";
        const data = await fetchJson(`/workspaces/browse${q}`);
        if (data?.path !== undefined) return data as BrowseResult;
        return null;
      },

      loadSafeRoots: async () => {
        const generation = accountGeneration;
        const requestId = ++latestSafeRootsRequest;
        const data = await fetchJson("/workspaces/safe-roots");
        if (
          generation === accountGeneration &&
          requestId === latestSafeRootsRequest &&
          data?.roots &&
          Array.isArray(data.roots)
        ) {
          set({ safeRoots: data.roots as SafeRootSummary[] });
        }
      },

      listSshKeys: async () => {
        const data = await fetchJson("/auth/ssh-keys");
        return Array.isArray(data?.keys) ? (data.keys as string[]) : [];
      },

      uploadSshKey: async (file) => {
        const form = new FormData();
        form.append("key", file);
        try {
          const res = await fetch("/auth/ssh-keys", {
            method: "POST",
            body: form,
          });
          if (!res.ok) return null;
          const data = await res.json();
          return typeof data?.key_id === "string" ? data.key_id : null;
        } catch {
          return null;
        }
      },

      deleteSshKey: async (keyId) => {
        try {
          const res = await fetch(
            `/auth/ssh-keys/${encodeURIComponent(keyId)}`,
            { method: "DELETE" },
          );
          return res.ok;
        } catch {
          return false;
        }
      },

      testSsh: async (host, keyId) => {
        try {
          const res = await fetch("/workspaces/test-ssh", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ host, key_id: keyId ?? null }),
          });
          const data = await res.json().catch(() => ({}));
          return {
            ok: Boolean(data?.ok),
            message: String(
              data?.message ?? (res.ok ? "" : `Erro ${res.status}`),
            ),
          };
        } catch (e) {
          return {
            ok: false,
            message: e instanceof Error ? e.message : "Falha de rede.",
          };
        }
      },

      listCodespaces: async () => {
        const data = await fetchJson("/workspaces/codespaces");
        return {
          codespaces: Array.isArray(data?.codespaces)
            ? (data.codespaces as CodespaceSummary[])
            : [],
          available: data?.available !== false,
          message: typeof data?.message === "string" ? data.message : "",
        };
      },

      createRemote: async (body) => {
        const data = await fetchJson("/workspaces/create-remote", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (data?.status === "ok" && data.workspace) {
          const ws = data.workspace as WorkspaceInfo;
          set((s) => ({
            workspaces: [...s.workspaces.filter((w) => w.id !== ws.id), ws],
            active_id: ws.id,
          }));
          return ws;
        }
        return null;
      },
    }),
    {
      name: "vectora-workspaces",
      storage: createJSONStorage(() =>
        typeof window !== "undefined"
          ? localStorage
          : {
              getItem: () => null,
              setItem: () => {},
              removeItem: () => {},
            },
      ),
      // Persiste somente o id selecionado. A lista completa pode conter caminhos
      // privados de outro usuário e deve sempre vir da API autenticada.
      partialize: (state) => ({
        active_id: state.active_id,
      }),
      merge: (persisted, current) => ({
        ...current,
        active_id:
          persisted && typeof persisted === "object" && "active_id" in persisted
            ? ((persisted as { active_id?: string | null }).active_id ?? null)
            : null,
        workspaces: [],
        safeRoots: [],
      }),
    },
  ),
);
