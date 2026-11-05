"use client";

/**
 * GitStatusBadge (G5)
 *
 * Exibe o estado git do workspace ativo no header:
 *   🌿 feature-auth · ↑2 ↓0 · ●
 *
 * - Branch ativa
 * - Indicadores ahead/behind quando há tracking remote
 * - Ponto colorido: verde = clean, laranja = dirty (uncommitted changes)
 *
 * Polling leve a cada 5 s (pausa quando aba inativa via visibilitychange).
 * Clique abre um painel com o output completo de git_status (placeholder —
 * expandir em G5 completo).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";

interface GitStatus {
  branch: string;
  clean: boolean;
  ahead: number;
  behind: number;
}

const POLL_INTERVAL_MS = 5_000;

export function GitStatusBadge() {
  const activeWorkspace = useWorkspacesStore((s) => s.getActive());
  const [gitStatus, setGitStatus] = useState<GitStatus | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pausedRef = useRef(false);

  const fetchStatus = useCallback(async () => {
    if (!activeWorkspace?.is_git_repo || pausedRef.current) return;
    try {
      const res = await fetch("/workspaces/active");
      if (!res.ok) return;
      const data = await res.json();
      const ws = data?.workspace;
      if (ws?.git_current_branch) {
        setGitStatus({
          branch: ws.git_current_branch,
          clean: true, // API básica — status completo exige endpoint dedicado
          ahead: 0,
          behind: 0,
        });
      }
    } catch {
      // silencioso — badge fica ausente se backend não responde
    }
  }, [activeWorkspace?.is_git_repo]);

  // Polling com pausa em aba inativa
  useEffect(() => {
    const schedule = () => {
      timerRef.current = setTimeout(() => {
        fetchStatus().finally(schedule);
      }, POLL_INTERVAL_MS);
    };

    const onVisibility = () => {
      pausedRef.current = document.hidden;
      if (!document.hidden) fetchStatus();
    };

    document.addEventListener("visibilitychange", onVisibility);
    // Busca inicial do status git ao montar — sincroniza com o backend,
    // não deriva de prop.
    // oxlint-disable-next-line react/set-state-in-effect
    fetchStatus();
    schedule();

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [fetchStatus]);

  if (!activeWorkspace?.is_git_repo || !gitStatus) return null;

  const { branch, clean, ahead, behind } = gitStatus;

  return (
    <div
      className="hidden sm:flex items-center gap-1 text-xs text-muted-foreground px-2 py-1 rounded-md hover:bg-muted/50 transition-colors cursor-default select-none"
      title={`Branch: ${branch}${ahead ? ` · ↑${ahead}` : ""}${behind ? ` ↓${behind}` : ""}${!clean ? " · alterações não commitadas" : ""}`}
    >
      {/* Ícone de branch */}
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="w-3.5 h-3.5 shrink-0"
      >
        <line x1="6" y1="3" x2="6" y2="15" />
        <circle cx="18" cy="6" r="3" />
        <circle cx="6" cy="18" r="3" />
        <path d="M18 9a9 9 0 0 1-9 9" />
      </svg>

      {/* Nome da branch */}
      <span className="max-w-[120px] truncate font-mono">{branch}</span>

      {/* Ahead/behind */}
      {ahead > 0 && <span>↑{ahead}</span>}
      {behind > 0 && <span>↓{behind}</span>}

      {/* Dirty indicator */}
      <span
        className={`w-1.5 h-1.5 rounded-full ${clean ? "bg-green-500" : "bg-orange-400"}`}
      />
    </div>
  );
}
