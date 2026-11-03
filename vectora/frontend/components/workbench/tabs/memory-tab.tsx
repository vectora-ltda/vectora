"use client";

/**
 * MemoryTab — "Memória da sessão": o que o Vectora está recuperando e já sabe.
 *
 * Estilo deep research: uma timeline de **atividade** (indexações RAG em
 * progresso e buscas/fetch web em andamento) seguida do **contexto recuperado**
 * (trechos da base de conhecimento + resultados web) em pílulas expansíveis.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Brain,
  ChevronRight,
  Database,
  Folder,
  Globe,
  Loader2,
  Plus,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useThreadMessages } from "@/lib/hooks/chat/use-thread-messages";
import { useRagJobsStore } from "@/lib/stores/rag-jobs-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { MarkdownView } from "@/components/workbench/markdown-view";
import { Switch } from "@/components/ui/switch";
import { WorkspaceTrustDialog } from "@/components/sidebar/workspace-trust-dialog";
import {
  RagSettingsButton,
  useRagSettings,
} from "@/components/workbench/rag-settings-panel";
import { WorkbenchSettingsSurface } from "@/components/workbench/settings/workbench-settings-surface";
import { memorySettings } from "@/components/workbench/settings/workbench-settings-registry";
import { m } from "@/lib/paraglide/messages";

interface RagBucketEntry {
  id: string;
  name: string;
  description_md: string;
  source_path: string | null;
  created_at: string;
  active: boolean;
}

/** Painel de buckets do workspace — lista + toggle ativo/inativo + remover +
 * atalho pra indexar pasta nova sem depender do composer de chat
 * (`GET/PATCH/DELETE /workspaces/{id}/rag/buckets`). */
function useWorkspaceRagBuckets(workspaceId: string | undefined) {
  const [buckets, setBuckets] = useState<RagBucketEntry[]>([]);

  const refetch = useCallback(() => {
    if (!workspaceId) {
      setBuckets([]);
      return;
    }
    fetch(`/workspaces/${encodeURIComponent(workspaceId)}/rag/buckets`)
      .then((r) => (r.ok ? r.json() : []))
      .then((data: RagBucketEntry[]) =>
        setBuckets(Array.isArray(data) ? data : []),
      )
      .catch(() => setBuckets([]));
  }, [workspaceId]);

  // Busca de dados ao montar (I/O de rede), não estado derivado de prop —
  // uso correto de efeito, ver justificativa em use-context-graph.ts.
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    refetch();
  }, [refetch]);

  return { buckets, refetch };
}

function BucketsPanel() {
  const workspaceId = useWorkspacesStore((s) => s.getActive()?.id);
  const { buckets, refetch } = useWorkspaceRagBuckets(workspaceId);
  const [ingestOpen, setIngestOpen] = useState(false);

  async function handleToggle(bucketId: string, active: boolean) {
    if (!workspaceId) return;
    await fetch(
      `/workspaces/${encodeURIComponent(workspaceId)}/rag/buckets/${encodeURIComponent(bucketId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active }),
      },
    );
    refetch();
  }

  async function handleRemove(bucketId: string) {
    if (!workspaceId) return;
    if (!window.confirm(m.workbench_memory_buckets_remove_confirm())) return;
    await fetch(
      `/workspaces/${encodeURIComponent(workspaceId)}/rag/buckets/${encodeURIComponent(bucketId)}`,
      { method: "DELETE" },
    );
    refetch();
  }

  if (!workspaceId) return null;

  return (
    <section className="space-y-1.5">
      <div className="flex items-center justify-between">
        <h3 className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          <Folder className="h-3 w-3" />
          {m.workbench_memory_buckets_title()}
        </h3>
        <button
          type="button"
          onClick={() => setIngestOpen(true)}
          className="flex items-center gap-1 text-[10px] text-primary hover:underline"
        >
          <Plus className="h-3 w-3" />
          {m.workbench_memory_buckets_index_button()}
        </button>
      </div>
      {buckets.length === 0 ? (
        <p className="px-2.5 py-2 text-xs text-muted-foreground">
          {m.workbench_memory_buckets_empty()}
        </p>
      ) : (
        buckets.map((bucket) => (
          <div
            key={bucket.id}
            className="flex items-center gap-2 rounded-lg border border-border/60 bg-card/30 px-2.5 py-2"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium text-foreground">
                {bucket.name}
              </p>
              {bucket.source_path && (
                <p className="truncate text-[10px] text-muted-foreground">
                  {bucket.source_path}
                </p>
              )}
            </div>
            <Switch
              checked={bucket.active}
              onCheckedChange={(checked) =>
                void handleToggle(bucket.id, checked)
              }
            />
            <button
              type="button"
              onClick={() => void handleRemove(bucket.id)}
              aria-label={m.workbench_memory_buckets_remove()}
              className="shrink-0 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))
      )}
      <WorkspaceTrustDialog
        open={ingestOpen}
        onOpenChange={(open) => {
          setIngestOpen(open);
          if (!open) refetch();
        }}
        mode="ingest"
      />
    </section>
  );
}

interface WorkspaceRagCollection {
  name: string;
  count: number;
}

/** RAG é escopo de workspace (LanceDB persiste entre sessões), não de
 * thread — consulta GET /rag/workspace-summary pra saber o que já está
 * indexado no workspace ATIVO, independente de `ragCitations` da thread. */
function useWorkspaceRagSummary(workspaceId: string | undefined) {
  const [collections, setCollections] = useState<WorkspaceRagCollection[]>([]);

  useEffect(() => {
    if (!workspaceId) {
      // Sincroniza com o fetch abaixo — sem workspace não há resumo a
      // buscar.
      // oxlint-disable-next-line react/set-state-in-effect
      setCollections([]);
      return;
    }
    let alive = true;
    void (async () => {
      try {
        const res = await fetch(
          `/rag/workspace-summary?workspace_id=${encodeURIComponent(workspaceId)}`,
        );
        if (!res.ok || !alive) return;
        const data = (await res.json()) as {
          collections?: WorkspaceRagCollection[];
        };
        if (alive) {
          setCollections(
            Array.isArray(data.collections) ? data.collections : [],
          );
        }
      } catch {
        if (alive) setCollections([]);
      }
    })();
    return () => {
      alive = false;
    };
  }, [workspaceId]);

  return collections;
}

interface JourneyFact {
  key: string;
  content: string;
  source: string;
  updated_at: string;
}

interface JourneySkill {
  id: string;
  name: string;
  description: string;
  installed_at: string;
}

/** O que o Remember já aprendeu sobre o usuário (fatos com tag `user_model` +
 *  skills geradas pelo learning loop). Só leitura — editar memória tem seu
 *  próprio fluxo pelas tools/painel de configurações. */
function useJourney() {
  const [facts, setFacts] = useState<JourneyFact[]>([]);
  const [skills, setSkills] = useState<JourneySkill[]>([]);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const res = await fetch("/memory/journey");
        if (!res.ok || !alive) return;
        const data = (await res.json()) as {
          facts?: JourneyFact[];
          skills?: JourneySkill[];
        };
        if (!alive) return;
        setFacts(Array.isArray(data.facts) ? data.facts : []);
        setSkills(Array.isArray(data.skills) ? data.skills : []);
      } catch {
        if (alive) {
          setFacts([]);
          setSkills([]);
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  return { facts, skills };
}

/** "O que aprendi sobre você" — só leitura: editar/apagar memória já tem o
 *  fluxo próprio no painel de configurações, duplicar aqui daria dois lugares
 *  divergentes pra mesma ação. */
function JourneyPanel() {
  const { facts, skills } = useJourney();

  return (
    <section className="space-y-1.5">
      <h3 className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        <Sparkles className="h-3 w-3" />
        {m.workbench_memory_journey_title()}
      </h3>
      {facts.length === 0 && skills.length === 0 ? (
        <p className="px-2.5 py-2 text-xs text-muted-foreground">
          {m.workbench_memory_journey_empty()}
        </p>
      ) : (
        <>
          {facts.map((fact) => (
            <div
              key={fact.key}
              className="rounded-lg border border-border/60 bg-card/30 px-2.5 py-2"
            >
              <p className="text-xs text-foreground">{fact.content}</p>
            </div>
          ))}
          {skills.map((skill) => (
            <div
              key={skill.id}
              className="rounded-lg border border-border/60 bg-card/30 px-2.5 py-2"
            >
              <p className="truncate text-xs font-medium text-foreground">
                {m.workbench_memory_journey_skill_label({ name: skill.name })}
              </p>
              <p className="truncate text-[10px] text-muted-foreground">
                {skill.description}
              </p>
            </div>
          ))}
        </>
      )}
    </section>
  );
}

interface RagSearchResult {
  content: string;
  collection: string;
  score?: number;
  relevance_score?: number;
  metadata?: { source?: string };
}

type UnifiedHitType = "fact" | "skill" | "rag_bucket";

interface UnifiedMemoryHit {
  type: UnifiedHitType;
  id: string;
  title: string;
  snippet: string;
  score?: number;
}

const UNIFIED_TYPE_ICON: Record<UnifiedHitType, typeof Brain> = {
  fact: Brain,
  skill: Sparkles,
  rag_bucket: Database,
};

/** Busca combinada em fatos + skills + buckets RAG (metadados, não chunks) —
 * GET /workspaces/{id}/memory/search. Complementa `useRagSearch` (que busca
 * conteúdo de chunk indexado), unificando os três tipos de memória do
 * produto numa única caixa de busca. */
function useUnifiedMemorySearch(
  workspaceId: string | undefined,
  query: string,
  types: UnifiedHitType[],
) {
  const [results, setResults] = useState<UnifiedMemoryHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  const typesKey = types.join(",");

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed || !workspaceId) {
      setResults(null);
      return;
    }
    let alive = true;
    setLoading(true);
    const handle = setTimeout(() => {
      void (async () => {
        try {
          const params = new URLSearchParams({ q: trimmed });
          if (typesKey) params.set("types", typesKey);
          const res = await fetch(
            `/workspaces/${encodeURIComponent(workspaceId)}/memory/search?${params.toString()}`,
          );
          if (!res.ok || !alive) return;
          const data = (await res.json()) as { hits?: UnifiedMemoryHit[] };
          if (alive) setResults(Array.isArray(data.hits) ? data.hits : []);
        } catch {
          if (alive) setResults([]);
        } finally {
          if (alive) setLoading(false);
        }
      })();
    }, 300);
    return () => {
      alive = false;
      clearTimeout(handle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, workspaceId, typesKey]);

  return { results, loading };
}

/** Busca direta do usuário na base RAG — POST /rag/search, mesma
 * `vector_search` que o agente usa. Não substitui `ragCitations` (o que o
 * agente já recuperou nesta resposta): é uma consulta explícita, disparada
 * pelo usuário, escopada ao workspace ativo. */
function useRagSearch(workspaceId: string | undefined) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<RagSearchResult[] | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      // Sincroniza com a busca debounçada abaixo — query vazia não tem
      // request pendente a esperar.
      // oxlint-disable-next-line react/set-state-in-effect
      setResults(null);
      return;
    }
    let alive = true;
    setLoading(true);
    const handle = setTimeout(() => {
      void (async () => {
        try {
          const res = await fetch("/rag/search", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: trimmed, workspace_id: workspaceId }),
          });
          if (!res.ok || !alive) return;
          const data = (await res.json()) as { results?: RagSearchResult[] };
          if (alive)
            setResults(Array.isArray(data.results) ? data.results : []);
        } catch {
          if (alive) setResults([]);
        } finally {
          if (alive) setLoading(false);
        }
      })();
    }, 300);
    return () => {
      alive = false;
      clearTimeout(handle);
    };
  }, [query, workspaceId]);

  return { query, setQuery, results, loading };
}

interface MemoryTabProps {
  threadId: string;
}

interface MemoryItem {
  id: string;
  kind: "rag" | "web";
  title: string;
  subtitle?: string;
  content: string;
}

const WEB_TOOLS = new Set(["web_search", "fetch_url", "web_fetch"]);

function toText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** Último segmento de um caminho (Windows ou POSIX). */
function baseName(path: string): string {
  const parts = path.split(/[/\\]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

function MemoryPill({ item }: { item: MemoryItem }) {
  const [open, setOpen] = useState(false);
  const Icon = item.kind === "rag" ? Database : Globe;
  return (
    <div className="rounded-lg border border-border/60 bg-card/30">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left"
        aria-expanded={open}
      >
        <ChevronRight
          className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`}
        />
        <Icon className="h-3.5 w-3.5 shrink-0 text-primary" />
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
          {item.title}
        </span>
        {item.subtitle && (
          <span className="shrink-0 truncate text-[10px] text-muted-foreground">
            {item.subtitle}
          </span>
        )}
      </button>
      {open && (
        <div className="max-h-80 overflow-auto border-t border-border/60 px-3 py-2">
          <MarkdownView content={item.content} />
        </div>
      )}
    </div>
  );
}

function MemorySearchBox({
  query,
  onChange,
}: {
  query: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="relative min-w-0 flex-1">
      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
      <input
        type="text"
        value={query}
        onChange={(e) => onChange(e.target.value)}
        placeholder={m.workbench_memory_search_placeholder()}
        className="w-full rounded-lg border border-border/60 bg-card/30 py-1.5 pl-7 pr-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary/50"
      />
    </div>
  );
}

export function MemoryTab({ threadId }: MemoryTabProps) {
  const [messages] = useThreadMessages(threadId);
  const activeWorkspaceId = useWorkspacesStore((s) => s.getActive()?.id);
  const jobs = useRagJobsStore((s) => s.jobs);
  const workspaceSummary = useWorkspaceRagSummary(activeWorkspaceId);
  const search = useRagSearch(activeWorkspaceId);
  const ragSettings = useRagSettings();
  const [unifiedTypeFilter, setUnifiedTypeFilter] = useState<UnifiedHitType[]>(
    [],
  );
  const unified = useUnifiedMemorySearch(
    activeWorkspaceId,
    search.query,
    unifiedTypeFilter,
  );

  // Jobs de indexação RAG do workspace ativo (atividade ao vivo).
  const ragJobs = useMemo(
    () =>
      Object.values(jobs).filter(
        (j) => !activeWorkspaceId || j.workspaceId === activeWorkspaceId,
      ),
    [jobs, activeWorkspaceId],
  );

  const { rag, web, activeWeb } = useMemo(() => {
    const ragItems: MemoryItem[] = [];
    const webItems: MemoryItem[] = [];
    const activeWebItems: string[] = [];
    const seenRag = new Set<string>();

    for (const msg of messages) {
      for (const c of msg.ragCitations ?? []) {
        const key = `${c.source}::${c.chunk.slice(0, 64)}`;
        if (seenRag.has(key)) continue;
        seenRag.add(key);
        ragItems.push({
          id: `rag-${ragItems.length}`,
          kind: "rag",
          title: c.source,
          subtitle: `[${c.index}]`,
          content: c.chunk,
        });
      }
      for (const call of msg.toolCalls ?? []) {
        if (!WEB_TOOLS.has(call.name)) continue;
        const args = call.args ?? {};
        const title =
          (typeof args.query === "string" && args.query) ||
          (typeof args.url === "string" && args.url) ||
          call.name;
        // Sem output ainda → busca em andamento (deep research ao vivo).
        if (call.output == null || call.output === "") {
          activeWebItems.push(title);
          continue;
        }
        webItems.push({
          id: `web-${call.id}`,
          kind: "web",
          title,
          subtitle: call.name === "fetch_url" ? "fetch" : "search",
          content: toText(call.output) || m.workbench_memory_no_result(),
        });
      }
    }
    return { rag: ragItems, web: webItems, activeWeb: activeWebItems };
  }, [messages]);

  const hasActivity = ragJobs.length > 0 || activeWeb.length > 0;
  const isEmpty = !hasActivity && rag.length === 0 && web.length === 0;
  const indexedInWorkspace = workspaceSummary.reduce(
    (total, c) => total + c.count,
    0,
  );

  const unifiedSection = search.query.trim() && (
    <section className="space-y-1.5">
      <div className="flex items-center justify-between">
        <h3 className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          <Search className="h-3 w-3" />
          {m.workbench_memory_unified_results()}
          {unified.results && ` (${unified.results.length})`}
        </h3>
        <div className="flex gap-1">
          {(
            [
              ["fact", m.workbench_memory_search_filter_fact()],
              ["skill", m.workbench_memory_search_filter_skill()],
              ["rag_bucket", m.workbench_memory_search_filter_rag_bucket()],
            ] as [UnifiedHitType, string][]
          ).map(([type, label]) => {
            const Icon = UNIFIED_TYPE_ICON[type];
            const active = unifiedTypeFilter.includes(type);
            return (
              <button
                key={type}
                type="button"
                aria-pressed={active}
                aria-label={label}
                title={label}
                onClick={() =>
                  setUnifiedTypeFilter((prev) =>
                    prev.includes(type)
                      ? prev.filter((t) => t !== type)
                      : [...prev, type],
                  )
                }
                className={`flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] ${
                  active
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border/60 text-muted-foreground"
                }`}
              >
                <Icon className="h-3 w-3" />
              </button>
            );
          })}
        </div>
      </div>
      {unified.loading && (
        <div className="flex items-center gap-2 px-2.5 py-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
          {m.workbench_memory_searching()}
        </div>
      )}
      {!unified.loading && unified.results?.length === 0 && (
        <p className="px-2.5 py-2 text-xs text-muted-foreground">
          {m.workbench_memory_search_no_results()}
        </p>
      )}
      {!unified.loading &&
        unified.results?.map((hit) => {
          const Icon = UNIFIED_TYPE_ICON[hit.type];
          return (
            <div
              key={`${hit.type}-${hit.id}`}
              className="rounded-lg border border-border/60 bg-card/30 px-2.5 py-2"
            >
              <div className="flex items-center gap-2">
                <Icon className="h-3.5 w-3.5 shrink-0 text-primary" />
                <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
                  {hit.title}
                </span>
              </div>
              {hit.snippet && (
                <p className="mt-1 truncate text-[10px] text-muted-foreground">
                  {hit.snippet}
                </p>
              )}
            </div>
          );
        })}
    </section>
  );

  const searchSection = search.query.trim() && (
    <section className="space-y-1.5">
      <h3 className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        <Search className="h-3 w-3" />
        {m.workbench_memory_search_results()}
        {search.results && ` (${search.results.length})`}
      </h3>
      {search.loading && (
        <div className="flex items-center gap-2 px-2.5 py-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
          {m.workbench_memory_searching()}
        </div>
      )}
      {!search.loading && search.results?.length === 0 && (
        <p className="px-2.5 py-2 text-xs text-muted-foreground">
          {m.workbench_memory_search_no_results()}
        </p>
      )}
      {!search.loading &&
        search.results?.map((r, i) => (
          <MemoryPill
            key={`search-${i}`}
            item={{
              id: `search-${i}`,
              kind: "rag",
              title: r.metadata?.source ?? r.collection,
              subtitle: r.collection,
              content: r.content,
            }}
          />
        ))}
    </section>
  );

  if (isEmpty) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex shrink-0 items-center gap-2 px-3 pt-3">
          <MemorySearchBox query={search.query} onChange={search.setQuery} />
          <RagSettingsButton
            open={ragSettings.open}
            onToggle={ragSettings.toggle}
          />
        </div>
        <WorkbenchSettingsSurface
          descriptor={memorySettings}
          context={{ threadId, workspaceId: activeWorkspaceId ?? null }}
          open={ragSettings.open}
          onOpenChange={(open) =>
            open ? ragSettings.toggle() : ragSettings.close()
          }
          testId="rag-settings-panel"
        />
        <div className="flex-1 space-y-4 overflow-auto px-3 py-3">
          <BucketsPanel />
          <JourneyPanel />
          {unifiedSection}
          {searchSection}
          {!searchSection && !unifiedSection && (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
              <Brain className="h-8 w-8 shrink-0 text-muted-foreground/40" />
              <div className="max-w-[240px]">
                <p className="text-sm font-medium text-foreground">
                  {indexedInWorkspace > 0
                    ? m.workbench_memory_indexed_title()
                    : m.workbench_memory_empty_title()}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  {indexedInWorkspace > 0
                    ? m.workbench_memory_indexed_desc({ n: indexedInWorkspace })
                    : m.workbench_memory_empty_desc()}
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-2 px-3 pt-3">
        <MemorySearchBox query={search.query} onChange={search.setQuery} />
        <RagSettingsButton
          open={ragSettings.open}
          onToggle={ragSettings.toggle}
        />
      </div>
      <WorkbenchSettingsSurface
        descriptor={memorySettings}
        context={{ threadId, workspaceId: activeWorkspaceId ?? null }}
        open={ragSettings.open}
        onOpenChange={(open) =>
          open ? ragSettings.toggle() : ragSettings.close()
        }
        testId="rag-settings-panel"
      />
      <div className="flex-1 space-y-4 overflow-auto px-3 pb-3 pt-3">
        <BucketsPanel />
        <JourneyPanel />
        {unifiedSection}
        {searchSection}
        {hasActivity && (
          <section className="space-y-1.5">
            <h3 className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              {m.workbench_memory_activity()}
            </h3>
            {ragJobs.map((job) => {
              const pct =
                job.total > 0
                  ? Math.min(100, Math.round((job.processed / job.total) * 100))
                  : job.status === "done"
                    ? 100
                    : 5;
              const stalled =
                job.status === "paused" || job.status === "failed";
              return (
                <div
                  key={job.jobId}
                  className="rounded-lg border border-border/60 bg-card/30 px-2.5 py-2"
                >
                  <div className="flex items-center gap-2">
                    {job.status === "done" ? (
                      <Database className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
                    ) : stalled ? (
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" />
                    ) : (
                      <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
                    )}
                    <span className="min-w-0 flex-1 truncate text-xs text-foreground">
                      {m.workbench_memory_indexing()} {baseName(job.path)}
                    </span>
                    <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
                      {job.processed}/{job.total}
                    </span>
                  </div>
                  {stalled && job.errorReason ? (
                    <p className="mt-1.5 text-[11px] leading-snug text-amber-600 dark:text-amber-400">
                      {job.errorReason}
                    </p>
                  ) : (
                    <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted/60">
                      <div
                        className="h-full bg-primary transition-all duration-300"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  )}
                </div>
              );
            })}
            {activeWeb.map((query, i) => (
              <div
                key={`active-web-${i}`}
                className="flex items-center gap-2 rounded-lg border border-border/60 bg-card/30 px-2.5 py-2"
              >
                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
                <span className="min-w-0 flex-1 truncate text-xs text-foreground">
                  {m.workbench_memory_searching()} {query}
                </span>
              </div>
            ))}
          </section>
        )}

        {rag.length > 0 && (
          <section className="space-y-1.5">
            <h3 className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              <Database className="h-3 w-3" />
              {m.workbench_memory_group_rag()} ({rag.length})
            </h3>
            {rag.map((item) => (
              <MemoryPill key={item.id} item={item} />
            ))}
          </section>
        )}
        {web.length > 0 && (
          <section className="space-y-1.5">
            <h3 className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              <Globe className="h-3 w-3" />
              {m.workbench_memory_group_web()} ({web.length})
            </h3>
            {web.map((item) => (
              <MemoryPill key={item.id} item={item} />
            ))}
          </section>
        )}
      </div>
    </div>
  );
}
