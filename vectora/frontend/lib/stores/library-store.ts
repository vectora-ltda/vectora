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

const TTL_MS = 5 * 60 * 1000;

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

async function fetchSkillsCatalog(q: string): Promise<CatalogSkill[]> {
  const qs = q ? `?${new URLSearchParams({ q })}` : "";
  const res = await fetch(`/skills/catalog${qs}`);
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  const data = (await res.json()) as { entries?: CatalogSkill[] };
  return data.entries ?? [];
}

async function fetchMemoryCatalog(q: string): Promise<MemoryBucket[]> {
  const qs = q ? `?${new URLSearchParams({ q })}` : "";
  const res = await fetch(`/rag-library/catalog${qs}`);
  if (!res.ok) throw new Error(`Erro ${res.status}`);
  return res.json();
}

interface LibraryStoreState {
  mcpItems: MCPConnector[];
  mcpInstalledIds: Set<string>;
  mcpLoading: boolean;
  mcpFetchedAt: number | null;
  mcpQuery: string;
  mcpError: string | null;

  skillsItems: CatalogSkill[];
  skillsLoading: boolean;
  skillsFetchedAt: number | null;
  skillsQuery: string;
  skillsError: string | null;

  memoryItems: MemoryBucket[];
  memoryLoading: boolean;
  memoryFetchedAt: number | null;
  memoryQuery: string;
  memoryError: string | null;

  ensureMcpLoaded: (q?: string) => Promise<void>;
  invalidateMcp: () => void;
  ensureSkillsLoaded: (q?: string) => Promise<void>;
  invalidateSkills: () => void;
  ensureMemoryLoaded: (q?: string) => Promise<void>;
  invalidateMemory: () => void;
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

  skillsItems: [],
  skillsLoading: false,
  skillsFetchedAt: null,
  skillsQuery: "",
  skillsError: null,

  memoryItems: [],
  memoryLoading: false,
  memoryFetchedAt: null,
  memoryQuery: "",
  memoryError: null,

  ensureMcpLoaded: async (q = "") => {
    const s = get();
    if (s.mcpLoading || (isFresh(s.mcpFetchedAt) && s.mcpQuery === q)) return;
    set({ mcpLoading: true });
    try {
      const [items, installedIds] = await Promise.all([
        fetchMcpRegistry(q),
        fetchMcpInstalledIds(),
      ]);
      set({
        mcpItems: items,
        mcpInstalledIds: installedIds,
        mcpFetchedAt: Date.now(),
        mcpQuery: q,
        mcpError: null,
      });
    } catch {
      set({ mcpError: m.library_mcp_error_search() });
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
      const items = await fetchSkillsCatalog(q);
      set({
        skillsItems: items,
        skillsFetchedAt: Date.now(),
        skillsQuery: q,
        skillsError: null,
      });
    } catch {
      set({ skillsError: m.library_skills_catalog_error_search() });
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
      set({ memoryError: m.library_memory_error_search() });
    } finally {
      set({ memoryLoading: false });
    }
  },

  invalidateMemory: () => set({ memoryFetchedAt: null }),
}));
