"use client";

import { useEffect, useMemo, useState } from "react";
import { useTheme } from "next-themes";
import { GraphCanvas, darkTheme, lightTheme } from "reagraph";
import type { GraphEdge, GraphNode } from "reagraph";
import { Loader2, Search, Waypoints, X } from "lucide-react";

import type {
  GraphQueryResult,
  RawGraphData,
  RawGraphLink,
  RawGraphNode,
} from "@/lib/hooks/use-context-graph";
import { m } from "@/lib/paraglide/messages";

interface ContextGraphViewerProps {
  fetchGraphData: () => Promise<RawGraphData | null>;
  pathBetween: (
    source: string,
    target: string,
  ) => Promise<GraphQueryResult | null>;
  onExplainNode: (label: string) => void;
  onAffectedNode: (label: string) => void;
}

// Mesma paleta de 10 cores Tableau usada pelo exportador HTML original
// (backend/context_graph/export.py) — mantém as cores por comunidade
// consistentes entre a visualização nativa e o crédito legado.
const COMMUNITY_PALETTE = [
  "#4E79A7",
  "#F28E2B",
  "#E15759",
  "#76B7B2",
  "#59A14F",
  "#EDC948",
  "#B07AA1",
  "#FF9DA7",
  "#9C755F",
  "#BAB0AC",
];

function communityColor(cid: number | null | undefined): string {
  if (cid == null) return "var(--color-muted-foreground)";
  return COMMUNITY_PALETTE[((cid % 10) + 10) % 10];
}

interface CommunityInfo {
  id: number;
  name: string;
  count: number;
  color: string;
}

export function ContextGraphViewer({
  fetchGraphData,
  pathBetween,
  onExplainNode,
  onAffectedNode,
}: ContextGraphViewerProps) {
  const { resolvedTheme } = useTheme();
  const [data, setData] = useState<RawGraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [hiddenCommunities, setHiddenCommunities] = useState<Set<number>>(
    new Set(),
  );
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<RawGraphNode | null>(null);
  const [pathMode, setPathMode] = useState(false);
  const [pathSource, setPathSource] = useState<RawGraphNode | null>(null);
  const [pathResult, setPathResult] = useState<GraphQueryResult | null>(null);
  const [pathLoading, setPathLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchGraphData().then((result) => {
      if (!cancelled) {
        setData(result);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- só uma vez na montagem, a aba remonta quando o grafo é reconstruído
  }, []);

  const rawNodes = useMemo(() => data?.nodes ?? [], [data]);
  const rawLinks = useMemo(() => data?.links ?? [], [data]);

  const communities = useMemo<CommunityInfo[]>(() => {
    const counts = new Map<number, { name: string; count: number }>();
    for (const n of rawNodes) {
      if (n.community == null) continue;
      const cur = counts.get(n.community);
      if (cur) cur.count += 1;
      else
        counts.set(n.community, {
          name: n.community_name ?? `Community ${n.community}`,
          count: 1,
        });
    }
    return [...counts.entries()]
      .toSorted((a, b) => a[0] - b[0])
      .map(([id, v]) => ({
        id,
        name: v.name,
        count: v.count,
        color: communityColor(id),
      }));
  }, [rawNodes]);

  const searchNorm = search.trim().toLowerCase();
  const matchedIds = useMemo(() => {
    if (!searchNorm) return null;
    return new Set(
      rawNodes
        .filter((n) => (n.label ?? n.id).toLowerCase().includes(searchNorm))
        .map((n) => n.id),
    );
  }, [rawNodes, searchNorm]);

  const visibleNodeIds = useMemo(() => {
    const ids = new Set<string>();
    for (const n of rawNodes) {
      if (n.community != null && hiddenCommunities.has(n.community)) continue;
      ids.add(n.id);
    }
    return ids;
  }, [rawNodes, hiddenCommunities]);

  const nodes: GraphNode[] = useMemo(
    () =>
      rawNodes
        .filter((n) => visibleNodeIds.has(n.id))
        .map((n) => ({
          id: n.id,
          label: n.label ?? n.id,
          cluster: n.community != null ? String(n.community) : undefined,
          fill: communityColor(n.community),
          size: matchedIds?.has(n.id) ? 12 : 7,
          data: n,
        })),
    [rawNodes, visibleNodeIds, matchedIds],
  );

  const edges: GraphEdge[] = useMemo(
    () =>
      rawLinks
        .filter(
          (l: RawGraphLink) =>
            visibleNodeIds.has(l.source) && visibleNodeIds.has(l.target),
        )
        .map((l: RawGraphLink, i: number) => ({
          id: `e${i}-${l.source}-${l.target}`,
          source: l.source,
          target: l.target,
          label: l.relation ?? undefined,
        })),
    [rawLinks, visibleNodeIds],
  );

  const allHidden =
    communities.length > 0 && hiddenCommunities.size === communities.length;
  const noneHidden = hiddenCommunities.size === 0;

  const pathNodeIds = useMemo(
    () => pathResult?.nodes.map((n) => n.id) ?? [],
    [pathResult],
  );

  function togglePathMode() {
    setPathMode((v) => !v);
    setPathSource(null);
    setPathResult(null);
  }

  function clearPath() {
    setPathSource(null);
    setPathResult(null);
  }

  function handleNodeClick(node: RawGraphNode) {
    if (!pathMode) {
      setSelected(node);
      return;
    }
    if (!pathSource) {
      setPathSource(node);
      setPathResult(null);
      return;
    }
    if (pathSource.id === node.id) return;
    setPathLoading(true);
    pathBetween(pathSource.id, node.id)
      .then((result) => setPathResult(result))
      .finally(() => setPathLoading(false));
  }

  function toggleCommunity(id: number) {
    setHiddenCommunities((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    // Semântica de checkbox tri-state padrão: a partir de indeterminado (ou
    // totalmente desmarcado), o clique avança pra "tudo visível" — só some
    // tudo quando já estava tudo visível antes do clique.
    setHiddenCommunities(
      noneHidden ? new Set(communities.map((c) => c.id)) : new Set(),
    );
  }

  if (loading) {
    return (
      <div className="flex-1 min-h-0 flex items-center justify-center text-muted-foreground text-sm gap-2">
        <Loader2 className="h-4 w-4 animate-spin" />
        {m.graph_building()}
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 flex">
      <div className="flex-1 min-h-0 relative">
        {/* Busca — filtra por label e amplia os nós correspondentes no canvas. */}
        <div className="absolute top-2 left-2 z-10 flex items-center gap-1.5 bg-card/90 backdrop-blur border border-border/60 rounded px-2 py-1">
          <Search className="h-3 w-3 text-muted-foreground shrink-0" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={m.graph_search_placeholder()}
            data-testid="graph-search-input"
            className="bg-transparent text-xs outline-none w-36 placeholder:text-muted-foreground/60"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              aria-label={m.graph_search_clear()}
              className="text-muted-foreground hover:text-foreground"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>

        {/* Modo caminho — clique em dois nós pra destacar o menor caminho
            entre eles (POST /path, já existia no backend sem UI). */}
        <div className="absolute top-2 right-2 z-10 flex items-center gap-1.5">
          <button
            onClick={togglePathMode}
            data-testid="graph-path-toggle"
            aria-pressed={pathMode}
            className={`flex items-center gap-1 text-xs px-2 py-1 rounded border backdrop-blur ${
              pathMode
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-card/90 text-muted-foreground border-border/60 hover:text-foreground"
            }`}
          >
            <Waypoints className="h-3 w-3" />
            {m.graph_path_mode_button()}
          </button>
        </div>

        {pathMode && (
          <div
            data-testid="graph-path-hint"
            className="absolute top-11 right-2 z-10 max-w-xs bg-card/90 backdrop-blur border border-border/60 rounded px-2 py-1.5 text-xs text-muted-foreground"
          >
            {pathLoading
              ? m.graph_building()
              : pathResult
                ? pathResult.answer
                : pathSource
                  ? m.graph_path_pick_target({
                      source: String(pathSource.label ?? pathSource.id),
                    })
                  : m.graph_path_pick_source()}
            {(pathSource || pathResult) && (
              <button
                onClick={clearPath}
                className="ml-2 underline hover:text-foreground"
              >
                {m.graph_path_clear()}
              </button>
            )}
          </div>
        )}

        <div data-testid="graph-canvas" className="absolute inset-0">
          <GraphCanvas
            nodes={nodes}
            edges={edges}
            theme={resolvedTheme === "dark" ? darkTheme : lightTheme}
            clusterAttribute="cluster"
            layoutType="forceDirected2d"
            labelType="auto"
            selections={selected ? [selected.id] : []}
            actives={pathNodeIds}
            onNodeClick={(n) => handleNodeClick(n.data as RawGraphNode)}
            onCanvasClick={() => setSelected(null)}
          />
        </div>

        {/* Painel de info do nó selecionado — sobrepõe o canto inferior. */}
        {selected && (
          <div
            data-testid="graph-node-info"
            className="absolute bottom-2 left-2 right-2 max-w-sm bg-card border border-border/60 rounded p-2.5 text-xs space-y-1.5 shadow-lg"
          >
            <div className="flex items-start justify-between gap-2">
              <p className="font-medium text-foreground break-all">
                {selected.label ?? selected.id}
              </p>
              <button
                onClick={() => setSelected(null)}
                aria-label={m.graph_search_clear()}
                className="text-muted-foreground hover:text-foreground shrink-0"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
            {selected.source_file && (
              <p className="text-muted-foreground truncate">
                {selected.source_file}
              </p>
            )}
            {selected.community_name && (
              <p className="flex items-center gap-1.5 text-muted-foreground">
                <span
                  className="h-2 w-2 rounded-full shrink-0"
                  style={{
                    backgroundColor: communityColor(selected.community),
                  }}
                />
                {selected.community_name}
              </p>
            )}
            <div className="flex gap-2 pt-1">
              <button
                onClick={() =>
                  onExplainNode(String(selected.label ?? selected.id))
                }
                className="text-xs px-2 py-0.5 rounded bg-muted hover:bg-muted/80 text-muted-foreground hover:text-foreground transition-colors"
              >
                {m.graph_explain_node_button()}
              </button>
              <button
                onClick={() =>
                  onAffectedNode(String(selected.label ?? selected.id))
                }
                className="text-xs px-2 py-0.5 rounded bg-muted hover:bg-primary/20 text-muted-foreground hover:text-primary transition-colors"
              >
                {m.graph_affected_button()}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Painel de comunidades — mesma paleta/estrutura do exportador HTML,
          portada pro componente nativo. */}
      {communities.length > 0 && (
        <div className="w-44 shrink-0 border-l border-border/40 overflow-y-auto px-2.5 py-2">
          <label className="flex items-center gap-1.5 text-xs font-medium mb-1.5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={noneHidden}
              ref={(el) => {
                if (el) el.indeterminate = !noneHidden && !allHidden;
              }}
              onChange={toggleSelectAll}
              className="accent-[var(--color-primary)]"
            />
            {m.graph_communities_select_all()}
          </label>
          <div className="space-y-1">
            {communities.map((c) => (
              <label
                key={c.id}
                className="flex items-center gap-1.5 text-xs cursor-pointer select-none"
              >
                <input
                  type="checkbox"
                  checked={!hiddenCommunities.has(c.id)}
                  onChange={() => toggleCommunity(c.id)}
                  className="accent-[var(--color-primary)]"
                />
                <span
                  className="h-2 w-2 rounded-full shrink-0"
                  style={{ backgroundColor: c.color }}
                />
                <span className="truncate flex-1 text-foreground">
                  {c.name}
                </span>
                <span className="text-muted-foreground/60">{c.count}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export type { GraphQueryResult };
