import { create } from "zustand";

/**
 * rag-jobs-store — acompanha jobs de indexação RAG disparados pela UI.
 *
 * `start()` chama `POST /workspaces/{id}/rag/ingest`. A atualização de
 * progresso chega principalmente via SSE (`applyEvent`, alimentado pelo
 * bridge `vectora:sse` — ver `sidebar.tsx`, que assina `useWebhookEvents`
 * filtrando `provider === "rag"`); o polling aqui é só uma rede de segurança
 * de baixa frequência para o caso do evento se perder (aba que perdeu
 * conexão SSE momentaneamente). O modal de indexação e a aba Memory leem
 * daqui, então a indexação continua visível mesmo após "Minimizar".
 */

export type RagJobStatus =
  "starting" | "indexing" | "done" | "failed" | "paused" | "no_files";

export interface RagJob {
  jobId: string;
  workspaceId: string;
  path: string;
  total: number;
  processed: number;
  failed: number;
  status: RagJobStatus;
  /** Motivo quando o worker pausou a indexação (ex.: rate limit do Cohere). */
  errorReason?: string;
}

interface RagJobsState {
  jobs: Record<string, RagJob>;
  /** Dispara a indexação; retorna o jobId (ou null em falha) e inicia o poll.
   * `fileTypes` aceita os 3 atalhos ou uma lista de extensões customizadas
   * (ex. `["xml"]`). `includeExts`/`excludeExts` (string CSV ou lista) são
   * filtros de extensão que sobrepõem o atalho. `bucketName` nomeia o bucket
   * criado (default: nome da pasta, decidido pelo backend quando omitido). */
  start: (
    workspaceId: string,
    path: string,
    fileTypes: "code" | "markdown" | "all" | string[],
    opts?: {
      includeExts?: string | string[];
      excludeExts?: string | string[];
      bucketName?: string;
    },
  ) => Promise<string | null>;
  /** Remove um job da lista (não cancela o processamento no backend). */
  dismiss: (jobId: string) => void;
  /** Aplica um evento SSE (`provider: "rag"`) recebido pelo bridge de webhooks. */
  applyEvent: (data: {
    job_id: string;
    total: number;
    processed: number;
    failed: number;
    status: RagJobStatus;
    error_reason?: string | null;
  }) => void;
}

// Rede de segurança — o caminho principal é o evento SSE (`applyEvent`).
const POLL_MS = 5000;
const timers: Record<string, ReturnType<typeof setInterval>> = {};

function stopPoll(jobId: string) {
  const t = timers[jobId];
  if (t) {
    clearInterval(t);
    delete timers[jobId];
  }
}

export const useRagJobsStore = create<RagJobsState>((set) => ({
  jobs: {},

  start: async (workspaceId, path, fileTypes, opts) => {
    let data: {
      job_id: string;
      total_chunks: number;
      status: string;
    };
    try {
      const res = await fetch(
        `/workspaces/${encodeURIComponent(workspaceId)}/rag/ingest`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            path,
            file_types: fileTypes,
            include_exts: opts?.includeExts || undefined,
            exclude_exts: opts?.excludeExts || undefined,
            bucket_name: opts?.bucketName || undefined,
          }),
        },
      );
      if (!res.ok) return null;
      data = await res.json();
    } catch {
      return null;
    }

    const jobId = data.job_id;
    set((s) => ({
      jobs: {
        ...s.jobs,
        [jobId]: {
          jobId,
          workspaceId,
          path,
          total: data.total_chunks ?? 0,
          processed: 0,
          failed: 0,
          status: data.status === "no_files" ? "no_files" : "indexing",
        },
      },
    }));

    if (data.status === "no_files") return jobId;

    const poll = async () => {
      try {
        const res = await fetch(
          `/workspaces/${encodeURIComponent(workspaceId)}/rag/jobs/${encodeURIComponent(jobId)}`,
          { credentials: "include" },
        );
        if (!res.ok) return;
        const st = (await res.json()) as {
          total: number;
          processed: number;
          failed: number;
          status: RagJobStatus;
          error_reason?: string | null;
        };
        set((s) => {
          const prev = s.jobs[jobId];
          if (!prev) return s;
          return {
            jobs: {
              ...s.jobs,
              [jobId]: {
                ...prev,
                total: st.total,
                processed: st.processed,
                failed: st.failed,
                status: st.status,
                errorReason: st.error_reason ?? undefined,
              },
            },
          };
        });
        // "paused" é terminal (worker arquivou a fila): para o poll.
        if (
          st.status === "done" ||
          st.status === "failed" ||
          st.status === "paused"
        )
          stopPoll(jobId);
      } catch {
        // mantém o poll; falha transitória de rede
      }
    };
    timers[jobId] = setInterval(() => void poll(), POLL_MS);
    void poll();
    return jobId;
  },

  dismiss: (jobId) => {
    stopPoll(jobId);
    set((s) => {
      const next = { ...s.jobs };
      delete next[jobId];
      return { jobs: next };
    });
  },

  applyEvent: (data) => {
    set((s) => {
      const prev = s.jobs[data.job_id];
      if (!prev) return s;
      return {
        jobs: {
          ...s.jobs,
          [data.job_id]: {
            ...prev,
            total: data.total,
            processed: data.processed,
            failed: data.failed,
            status: data.status,
            errorReason: data.error_reason ?? undefined,
          },
        },
      };
    });
    if (
      data.status === "done" ||
      data.status === "failed" ||
      data.status === "paused"
    ) {
      stopPoll(data.job_id);
    }
  },
}));
