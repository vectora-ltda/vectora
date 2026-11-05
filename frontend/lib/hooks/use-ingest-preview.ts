"use client";

import { useEffect, useRef, useState } from "react";

interface IngestPreviewResult {
  total: number;
  files: string[];
  loading: boolean;
}

interface IngestPreviewResponse {
  total: number;
  files: string[];
}

const DEBOUNCE_MS = 400;

/** Preview dos arquivos que os filtros de ingest bateriam, sem indexar nada.
 *
 * Debounça 400ms após qualquer mudança em `path`/filtros e descarta
 * respostas de requests obsoletas (corrida entre digitação rápida e
 * latência de rede) via flag `alive`. Nunca zera `total`/`files` no meio
 * da digitação — só atualiza quando uma resposta válida chega. */
export function useIngestPreview(
  workspaceId: string | null | undefined,
  path: string,
  fileTypesShortcut: "all" | "code" | "markdown",
  includeExts: string,
  excludeExts: string,
): IngestPreviewResult {
  const [total, setTotal] = useState(0);
  const [files, setFiles] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const requestSeq = useRef(0);

  useEffect(() => {
    const trimmedPath = path.trim();
    if (!workspaceId || !trimmedPath) {
      // Sincroniza com o efeito de debounce/fetch abaixo — sem workspace ou
      // path não há requisição a esperar.
      // oxlint-disable-next-line react/set-state-in-effect
      setLoading(false);
      return;
    }

    setLoading(true);
    let alive = true;
    const seq = ++requestSeq.current;
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const params = new URLSearchParams({
            path: trimmedPath,
            file_types: fileTypesShortcut,
          });
          if (includeExts.trim())
            params.set("include_exts", includeExts.trim());
          if (excludeExts.trim())
            params.set("exclude_exts", excludeExts.trim());

          const res = await fetch(
            `/workspaces/${encodeURIComponent(workspaceId)}/rag/ingest/preview?${params.toString()}`,
            { credentials: "include" },
          );
          if (!alive || seq !== requestSeq.current) return;
          if (!res.ok) {
            setTotal(0);
            setFiles([]);
            return;
          }
          const data = (await res.json()) as IngestPreviewResponse;
          if (!alive || seq !== requestSeq.current) return;
          setTotal(data.total);
          setFiles(data.files);
        } catch {
          if (alive && seq === requestSeq.current) {
            setTotal(0);
            setFiles([]);
          }
        } finally {
          if (alive && seq === requestSeq.current) setLoading(false);
        }
      })();
    }, DEBOUNCE_MS);

    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [workspaceId, path, fileTypesShortcut, includeExts, excludeExts]);

  return { total, files, loading };
}
