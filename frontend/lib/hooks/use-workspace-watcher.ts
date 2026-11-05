"use client";

/**
 * use-workspace-watcher — conecta ao endpoint SSE GET /workspaces/{id}/events
 * e sinaliza pendência no workbench-store quando um evento fs_changed chega.
 *
 * - Reconecta automaticamente em caso de erro (backoff 1s → 30s)
 * - Para quando o workspaceId muda ou é undefined
 * - Usa EventSource nativo do browser
 */

import { useEffect, useRef } from "react";
import { useWorkbenchStore } from "@/lib/stores/workbench-store";

const MIN_RECONNECT_MS = 1_000;
const MAX_RECONNECT_MS = 30_000;

export function useWorkspaceWatcher(workspaceId: string | undefined): void {
  const markPending = useWorkbenchStore((s) => s.markPending);
  const reconnectDelay = useRef(MIN_RECONNECT_MS);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!workspaceId) return;

    let cancelled = false;

    function connect() {
      if (cancelled) return;
      const es = new EventSource(
        `/workspaces/${encodeURIComponent(workspaceId!)}/events`,
      );
      esRef.current = es;

      es.addEventListener("message", (evt) => {
        try {
          const data = JSON.parse(evt.data as string) as {
            type: string;
            paths?: string[];
          };
          if (data.type === "fs_changed") {
            markPending(workspaceId!);
            reconnectDelay.current = MIN_RECONNECT_MS;
          }
        } catch {
          // ignorar linhas malformadas
        }
      });

      es.addEventListener("error", () => {
        es.close();
        esRef.current = null;
        if (cancelled) return;
        timeoutRef.current = setTimeout(() => {
          reconnectDelay.current = Math.min(
            reconnectDelay.current * 2,
            MAX_RECONNECT_MS,
          );
          connect();
        }, reconnectDelay.current);
      });
    }

    connect();

    return () => {
      cancelled = true;
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
      reconnectDelay.current = MIN_RECONNECT_MS;
    };
  }, [workspaceId, markPending]);
}
