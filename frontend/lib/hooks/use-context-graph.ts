"use client";

import { useCallback, useEffect, useState } from "react";

export interface GraphStatus {
  status:
    | "not_built"
    | "running"
    | "done"
    | "error"
    | "unknown"
    | "queued"
    | "paused";
  node_count?: number | null;
  edge_count?: number | null;
  error?: string | null;
  step?: number | null;
  step_total?: number | null;
  step_label?: string | null;
  files_total?: number | null;
  files_done?: number | null;
  files_list?: string[] | null;
}

export interface GraphReport {
  report: string;
}

/** Nó em graph.json (backend/context_graph/export.py::to_json). */
export interface RawGraphNode {
  id: string;
  label?: string;
  community?: number | null;
  community_name?: string | null;
  file_type?: string | null;
  source_file?: string | null;
  [key: string]: unknown;
}

/** Aresta em graph.json — chave "links", não "edges" (node_link_data). */
export interface RawGraphLink {
  source: string;
  target: string;
  relation?: string | null;
  confidence_score?: number | null;
  [key: string]: unknown;
}

export interface RawGraphData {
  nodes: RawGraphNode[];
  links: RawGraphLink[];
  hyperedges?: unknown[];
}

export interface GraphQueryResult {
  answer: string;
  nodes: RawGraphNode[];
  edges: RawGraphLink[];
}

function base(workspaceId: string): string {
  return `/workspaces/${encodeURIComponent(workspaceId)}/context-graph`;
}

async function asJson<T>(res: Response, fallback: T): Promise<T> {
  if (!res.ok) return fallback;
  try {
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

export function useContextGraph(workspaceId: string | null | undefined) {
  const [status, setStatus] = useState<GraphStatus>({ status: "unknown" });
  const [report, setReport] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchStatus = useCallback(async () => {
    if (!workspaceId) return;
    const res = await fetch(`${base(workspaceId)}/status`).catch(() => null);
    if (!res) return;
    const data = await asJson<GraphStatus>(res, { status: "unknown" });
    setStatus(data);
  }, [workspaceId]);

  const fetchReport = useCallback(async () => {
    if (!workspaceId) return;
    const res = await fetch(`${base(workspaceId)}/report`).catch(() => null);
    if (!res || !res.ok) return;
    const data = await asJson<GraphReport>(res, { report: "" });
    if (data.report) setReport(data.report);
  }, [workspaceId]);

  // Busca de dados (I/O de rede), não estado derivado de prop/state — o
  // caso que `set-state-in-effect` existe pra pegar é o oposto (copiar uma
  // prop pra um state local sem necessidade). Buscar ao montar/quando o
  // status muda é o uso correto de efeito segundo o próprio React:
  // https://react.dev/learn/you-might-not-need-an-effect#fetching-data
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    fetchStatus();
  }, [fetchStatus]);

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    if (status.status === "done") fetchReport();
  }, [status.status, fetchReport]);

  useEffect(() => {
    if (status.status !== "running") return;
    const id = setInterval(fetchStatus, 1500);
    return () => clearInterval(id);
  }, [status.status, fetchStatus]);

  const build = useCallback(
    async (
      opts: {
        model?: string;
        mode?: string;
        update?: boolean;
        resume?: boolean;
        fileTypes?: string[];
      } = {},
    ) => {
      if (!workspaceId) return;
      setLoading(true);
      setStatus({ status: "queued" });
      const res = await fetch(`${base(workspaceId)}/build`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: opts.model ?? "",
          mode: opts.mode ?? "semantic",
          update: opts.update ?? false,
          resume: opts.resume ?? false,
          file_types: opts.fileTypes ?? [],
        }),
      }).catch(() => null);
      setLoading(false);
      if (res?.ok) {
        setStatus({ status: "running" });
      }
    },
    [workspaceId],
  );

  const getHtmlUrl = useCallback(() => {
    if (!workspaceId) return null;
    return `${base(workspaceId)}/html`;
  }, [workspaceId]);

  const queryAffected = useCallback(
    async (nodeQuery: string, depth = 2): Promise<string> => {
      if (!workspaceId) return "";
      try {
        const res = await fetch(`${base(workspaceId)}/affected`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ node_query: nodeQuery, depth }),
        });
        if (!res.ok) return "";
        const data = await res.json();
        return (data as { answer?: string }).answer ?? "";
      } catch {
        return "";
      }
    },
    [workspaceId],
  );

  /** graph.json completo (nós/arestas/comunidades) — visualização nativa. */
  const fetchGraphData = useCallback(async (): Promise<RawGraphData | null> => {
    if (!workspaceId) return null;
    try {
      const res = await fetch(base(workspaceId));
      if (!res.ok) return null;
      return (await res.json()) as RawGraphData;
    } catch {
      return null;
    }
  }, [workspaceId]);

  const explainNode = useCallback(
    async (nodeId: string, depth = 1): Promise<GraphQueryResult | null> => {
      if (!workspaceId) return null;
      try {
        const res = await fetch(`${base(workspaceId)}/explain`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ node_id: nodeId, depth }),
        });
        if (!res.ok) return null;
        return (await res.json()) as GraphQueryResult;
      } catch {
        return null;
      }
    },
    [workspaceId],
  );

  const queryGraph = useCallback(
    async (question: string, topK = 10): Promise<GraphQueryResult | null> => {
      if (!workspaceId) return null;
      try {
        const res = await fetch(`${base(workspaceId)}/query`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, top_k: topK }),
        });
        if (!res.ok) return null;
        return (await res.json()) as GraphQueryResult;
      } catch {
        return null;
      }
    },
    [workspaceId],
  );

  const pathBetween = useCallback(
    async (
      source: string,
      target: string,
    ): Promise<GraphQueryResult | null> => {
      if (!workspaceId) return null;
      try {
        const res = await fetch(`${base(workspaceId)}/path`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source, target }),
        });
        if (!res.ok) return null;
        return (await res.json()) as GraphQueryResult;
      } catch {
        return null;
      }
    },
    [workspaceId],
  );

  const update = useCallback(
    async (
      opts: { model?: string; mode?: string; fileTypes?: string[] } = {},
    ) => {
      await build({ ...opts, update: true });
    },
    [build],
  );

  const resume = useCallback(
    async (
      opts: { model?: string; mode?: string; fileTypes?: string[] } = {},
    ) => {
      // Retoma um build pausado por quota: reusa o checkpoint AST e refaz só a
      // semântica (resume=true), em vez de reprocessar do zero.
      await build({ ...opts, resume: true });
    },
    [build],
  );

  const cancel = useCallback(async () => {
    if (!workspaceId) return;
    await fetch(`${base(workspaceId)}/build`, { method: "DELETE" }).catch(
      () => null,
    );
    setStatus({ status: "not_built" });
  }, [workspaceId]);

  return {
    status,
    report,
    loading,
    build,
    update,
    resume,
    cancel,
    queryAffected,
    fetchStatus,
    getHtmlUrl,
    fetchGraphData,
    explainNode,
    queryGraph,
    pathBetween,
  };
}
