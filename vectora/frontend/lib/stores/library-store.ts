import { create } from "zustand";

import { m } from "@/lib/paraglide/messages";

/**
 * library-store — cache compartilhado dos 3 catálogos da aba Library
 * (MCP/Skills/Memory), sobrevivendo ao unmount do AccordionContent do Radix
 * (que desmonta a seção inteira ao fechar o item). Sem isso, fechar e reabrir
 * qualquer seção refaz o fetch do zero mesmo com dado ainda fresco.
 */

export interface MCPConnector {
  id: string;
  name: string;
  description: string;
  readme?: string;
  icon?: string | null;
  install_cmd: string;
  env_vars: string[];
  homepage: string;
  category: string;
  vectora_verified: boolean;
  icon_url?: string | null;
  trust_state?:
    | "vectora_verified"
    | "publisher_signed"
    | "community_listed"
    | "unsigned"
    | "invalid"
    | "verification_unavailable";
  trust_reason?: string;
}

export interface CatalogSkill {
  id: string;
  name: string;
  description: string;
  source: string;
  vectora_verified?: boolean;
  verified?: boolean;
  catalog_source?: string;
  trust_state?: MCPConnector["trust_state"];
  trust_reason?: string;
  publisher?: string;
  signature_status?: string;
}

/**
 * Nível de confiança derivado de `vectora_verified`/`verified` — mesmo
 * par de colunas de `mcp_catalog`/`skills_catalog` (`services/migrations/
 * 0001_schema.sql`), aqui unificado num único rótulo pra badge. `builtin`
 * (selo oficial de curadoria/seed) sempre vence `verified` (curadoria de
 * admin sobre publicação de comunidade) — os dois nunca aparecem juntos
 * na mesma linha, mas a prioridade documenta a intenção mesmo assim.
 */
export type SkillTrustLevel = "builtin" | "verified" | "community";

export function skillTrustLevel(skill: CatalogSkill): SkillTrustLevel {
  if (skill.vectora_verified) return "builtin";
  if (skill.verified) return "verified";
  return "community";
}

export interface MemoryBucket {
  id: string;
  name: string;
  description: string;
  embed_model: string;
  verified: boolean;
  downloads_count: number;
  license?: string;
}

export interface VextExtension {
  id: string;
  name: string;
  description: string;
  publisher: string;
  icon?: string | null;
  native?: boolean;
  version: string;
  runtime: "node" | "python" | "none";
  platforms: string | string[];
  permissions: string;
  digest: string;
  status: "published" | "installed";
  frontend_entrypoint?: string | null;
  backend_entrypoint?: string | null;
  contributions?: {
    workbench?: Array<{
      id: string;
      title: string;
      icon?: string;
      entrypoint?: string;
    }>;
    shortcuts?: Array<{
      id: string;
      title: string;
      keybinding?: string;
      command: string;
    }>;
    footer?: Array<{ id: string; title: string; entrypoint?: string }>;
    [key: string]: unknown;
  };
}

export interface CatalogStatus {
  source: "mcp" | "skills";
  status: "never" | "ready" | "disabled" | "unavailable";
  last_synced_at: string | null;
  error: string | null;
}

const TTL_MS = 5 * 60 * 1000;

export const NATIVE_EXTENSION_IDS = new Set(["github", "gitlab"]);
const EXTENSION_DESCRIPTIONS: Record<string, string> = {
  eslint: m.library_extension_desc_eslint(),
  oxlint: m.library_extension_desc_oxlint(),
  precommit: m.library_extension_desc_precommit(),
  prettier: m.library_extension_desc_prettier(),
  pyright: m.library_extension_desc_pyright(),
  ruff: m.library_extension_desc_ruff(),
  ty: m.library_extension_desc_ty(),
};

async function fetchMcpRegistry(q: string): Promise<MCPConnector[]> {
  const qs = q ? `?${new URLSearchParams({ q })}` : "";
  const res = await fetch(`/mcp/registry${qs}`);
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json();
}

async function fetchMcpInstalledIds(): Promise<Set<string>> {
  const res = await fetch("/plugins");
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  const data = (await res.json()) as { servers?: { name: string }[] };
  return new Set((data.servers ?? []).map((s) => s.name));
}

async function fetchMcpStatus(): Promise<CatalogStatus> {
  const res = await fetch("/mcp/registry/status");
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json() as Promise<CatalogStatus>;
}

async function fetchSkillsCatalog(q: string): Promise<CatalogSkill[]> {
  const qs = q ? `?${new URLSearchParams({ q })}` : "";
  const res = await fetch(`/skills/catalog${qs}`);
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  const data = (await res.json()) as { entries?: CatalogSkill[] };
  return data.entries ?? [];
}

async function fetchSkillsStatus(): Promise<CatalogStatus> {
  const res = await fetch("/skills/catalog/status");
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json() as Promise<CatalogStatus>;
}

async function fetchMemoryCatalog(q: string): Promise<MemoryBucket[]> {
  const qs = q ? `?${new URLSearchParams({ q })}` : "";
  const res = await fetch(`/rag-library/catalog${qs}`);
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json();
}

async function fetchExtensionsCatalog(q: string): Promise<VextExtension[]> {
  const qs = q ? `?${new URLSearchParams({ q })}` : "";
  const res = await fetch(`/registry/extensions${qs}`);
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  const data = (await res.json()) as { entries?: VextExtension[] };
  return data.entries ?? [];
}

async function fetchInstalledExtensions(): Promise<{
  ids: Set<string>;
  items: VextExtension[];
}> {
  const res = await fetch("/vext/installed");
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  const data = (await res.json()) as {
    extensions?: {
      id: string;
      version: string;
      active?: boolean;
      manifest?: {
        name?: string;
        description?: string;
        icon?: string | null;
        publisher?: string;
        native?: boolean;
        runtime?: VextExtension["runtime"];
        platforms?: string[];
        permissions?: string[];
        integrity?: string | null;
        frontend_entrypoint?: string | null;
        backend_entrypoint?: string | null;
        contributions?: Record<string, unknown>;
      };
    }[];
  };
  const active = (data.extensions ?? []).filter(
    (item) => item.active !== false,
  );
  return {
    ids: new Set(active.map((item) => item.id)),
    items: active.map((item) => ({
      id: item.id,
      name: item.manifest?.name ?? item.id,
      description:
        item.manifest?.description ?? EXTENSION_DESCRIPTIONS[item.id] ?? "",
      icon: item.manifest?.icon
        ? `/vext/${encodeURIComponent(item.id)}/icon`
        : undefined,
      publisher:
        item.manifest?.publisher === "official"
          ? "Vectora"
          : (item.manifest?.publisher ?? "local"),
      native: item.manifest?.native ?? false,
      version: item.version,
      runtime: item.manifest?.runtime ?? "none",
      platforms: Array.isArray(item.manifest?.platforms)
        ? item.manifest.platforms.join(", ")
        : (item.manifest?.platforms ?? "any"),
      permissions: item.manifest?.permissions?.join(", ") ?? "",
      digest: item.manifest?.integrity ?? "",
      status: "installed",
      frontend_entrypoint: item.manifest?.frontend_entrypoint,
      backend_entrypoint: item.manifest?.backend_entrypoint,
      contributions: item.manifest
        ?.contributions as VextExtension["contributions"],
    })),
  };
}

async function installExtensionArtifact(
  extension: VextExtension,
): Promise<void> {
  const downloadUrl = `/registry/extensions/${encodeURIComponent(extension.id)}/download/${encodeURIComponent(extension.version)}`;
  const response = await fetch(downloadUrl);
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  const bytes = await response.blob();
  const form = new FormData();
  form.append("artifact", bytes, `${extension.id}-${extension.version}.vext`);
  const install = await fetch("/vext/install", { method: "POST", body: form });
  if (!install.ok) throw new Error(`Erro ${install.status}`);
}

interface LibraryStoreState {
  mcpItems: MCPConnector[];
  mcpInstalledIds: Set<string>;
  mcpLoading: boolean;
  mcpFetchedAt: number | null;
  mcpQuery: string;
  mcpError: string | null;
  mcpStatus: CatalogStatus;

  skillsItems: CatalogSkill[];
  skillsLoading: boolean;
  skillsFetchedAt: number | null;
  skillsQuery: string;
  skillsError: string | null;
  skillsStatus: CatalogStatus;

  memoryItems: MemoryBucket[];
  memoryLoading: boolean;
  memoryFetchedAt: number | null;
  memoryQuery: string;
  memoryError: string | null;

  extensionItems: VextExtension[];
  extensionLoading: boolean;
  extensionFetchedAt: number | null;
  extensionQuery: string;
  extensionError: string | null;
  extensionInstalledIds: Set<string>;
  extensionInstallingId: string | null;

  ensureMcpLoaded: (q?: string) => Promise<void>;
  invalidateMcp: () => void;
  ensureSkillsLoaded: (q?: string) => Promise<void>;
  invalidateSkills: () => void;
  ensureMemoryLoaded: (q?: string) => Promise<void>;
  invalidateMemory: () => void;
  ensureExtensionsLoaded: (q?: string) => Promise<void>;
  refreshInstalledExtensions: () => Promise<void>;
  invalidateExtensions: () => void;
  installExtension: (extension: VextExtension) => Promise<void>;
  uninstallExtension: (extensionId: string) => Promise<void>;
}

function isFresh(fetchedAt: number | null): boolean {
  return fetchedAt !== null && Date.now() - fetchedAt < TTL_MS;
}

export const useLibraryStore = create<LibraryStoreState>((set, get) => ({
  mcpItems: [],
  mcpInstalledIds: new Set(),
  mcpLoading: false,
  mcpFetchedAt: null,
  mcpQuery: "",
  mcpError: null,
  mcpStatus: {
    source: "mcp",
    status: "never",
    last_synced_at: null,
    error: null,
  },

  skillsItems: [],
  skillsLoading: false,
  skillsFetchedAt: null,
  skillsQuery: "",
  skillsError: null,
  skillsStatus: {
    source: "skills",
    status: "never",
    last_synced_at: null,
    error: null,
  },

  memoryItems: [],
  memoryLoading: false,
  memoryFetchedAt: null,
  memoryQuery: "",
  memoryError: null,
  extensionItems: [],
  extensionLoading: false,
  extensionFetchedAt: null,
  extensionQuery: "",
  extensionError: null,
  extensionInstalledIds: new Set(),
  extensionInstallingId: null,

  ensureMcpLoaded: async (q = "") => {
    const s = get();
    if (s.mcpLoading || (isFresh(s.mcpFetchedAt) && s.mcpQuery === q)) return;
    set({ mcpLoading: true });
    try {
      const items = await fetchMcpRegistry(q);
      const installedIds = await fetchMcpInstalledIds();
      const status = await fetchMcpStatus().catch(() => get().mcpStatus);
      set({
        mcpItems: items,
        mcpInstalledIds: installedIds,
        mcpFetchedAt: Date.now(),
        mcpQuery: q,
        mcpError: null,
        mcpStatus: status,
      });
    } catch {
      set({
        mcpError: m.library_mcp_error_search(),
        mcpStatus: {
          source: "mcp",
          status: "unavailable",
          last_synced_at: null,
          error: m.library_mcp_error_search(),
        },
      });
    } finally {
      set({ mcpLoading: false });
    }
  },

  invalidateMcp: () => set({ mcpFetchedAt: null }),

  ensureSkillsLoaded: async (q = "") => {
    const s = get();
    if (s.skillsLoading || (isFresh(s.skillsFetchedAt) && s.skillsQuery === q))
      return;
    set({ skillsLoading: true });
    try {
      const [items, status] = await Promise.all([
        fetchSkillsCatalog(q),
        fetchSkillsStatus().catch(() => get().skillsStatus),
      ]);
      set({
        skillsItems: items.filter(
          (item) =>
            !item.id.startsWith("vectora-") || item.catalog_source === "local",
        ),
        skillsFetchedAt: Date.now(),
        skillsQuery: q,
        skillsError: null,
        skillsStatus: status,
      });
    } catch {
      set({
        skillsError: m.library_skills_catalog_error_search(),
        skillsStatus: {
          source: "skills",
          status: "unavailable",
          last_synced_at: null,
          error: m.library_skills_catalog_error_search(),
        },
      });
    } finally {
      set({ skillsLoading: false });
    }
  },

  invalidateSkills: () => set({ skillsFetchedAt: null }),

  ensureMemoryLoaded: async (q = "") => {
    const s = get();
    if (s.memoryLoading || (isFresh(s.memoryFetchedAt) && s.memoryQuery === q))
      return;
    set({ memoryLoading: true });
    try {
      const items = await fetchMemoryCatalog(q);
      set({
        memoryItems: items,
        memoryFetchedAt: Date.now(),
        memoryQuery: q,
        memoryError: null,
      });
    } catch {
      set({ memoryError: m.library_memory_buckets_error_search() });
    } finally {
      set({ memoryLoading: false });
    }
  },

  invalidateMemory: () => set({ memoryFetchedAt: null }),

  ensureExtensionsLoaded: async (q = "") => {
    const s = get();
    if (
      s.extensionLoading ||
      (isFresh(s.extensionFetchedAt) && s.extensionQuery === q)
    )
      return;
    set({ extensionLoading: true });
    try {
      const [catalogResult, installedResult] = await Promise.allSettled([
        fetchExtensionsCatalog(q),
        fetchInstalledExtensions(),
      ]);
      const catalog =
        catalogResult.status === "fulfilled" ? catalogResult.value : [];
      const installed =
        installedResult.status === "fulfilled"
          ? installedResult.value
          : { ids: get().extensionInstalledIds, items: [] };
      const byId = new Map<string, VextExtension>();
      for (const item of catalog) byId.set(item.id, item);
      for (const item of installed.items) byId.set(item.id, item);
      set({
        extensionItems: [...byId.values()],
        extensionInstalledIds: installed.ids,
        extensionFetchedAt: Date.now(),
        extensionQuery: q,
        extensionError:
          catalogResult.status === "rejected" && installed.items.length === 0
            ? m.library_extensions_error_search()
            : null,
      });
    } catch {
      set({ extensionError: m.library_extensions_error_search() });
    } finally {
      set({ extensionLoading: false });
    }
  },

  refreshInstalledExtensions: async () => {
    try {
      const installed = await fetchInstalledExtensions();
      set((state) => {
        const byId = new Map<string, VextExtension>();
        for (const item of state.extensionItems) byId.set(item.id, item);
        for (const item of installed.items) byId.set(item.id, item);
        return {
          extensionInstalledIds: installed.ids,
          extensionItems: [...byId.values()],
        };
      });
    } catch {
      // The catalog remains usable when the local lifecycle endpoint is unavailable.
    }
  },

  invalidateExtensions: () => set({ extensionFetchedAt: null }),

  installExtension: async (extension) => {
    set({ extensionInstallingId: extension.id });
    try {
      await installExtensionArtifact(extension);
      set((state) => ({
        extensionInstalledIds: new Set(state.extensionInstalledIds).add(
          extension.id,
        ),
      }));
    } catch {
      set({ extensionError: m.library_extensions_error_install() });
    } finally {
      set({ extensionInstallingId: null });
    }
  },

  uninstallExtension: async (extensionId) => {
    set({ extensionInstallingId: extensionId });
    try {
      const response = await fetch(`/vext/${encodeURIComponent(extensionId)}`, {
        method: "DELETE",
      });
      if (!response.ok) throw new Error(`Erro ${response.status}`);
      set((state) => ({
        extensionInstalledIds: new Set(
          [...state.extensionInstalledIds].filter((id) => id !== extensionId),
        ),
        extensionItems: state.extensionItems.filter(
          (item) => !(item.id === extensionId && item.status === "installed"),
        ),
      }));
    } catch {
      set({ extensionError: m.library_extensions_error_uninstall() });
    } finally {
      set({ extensionInstallingId: null });
    }
  },
}));
