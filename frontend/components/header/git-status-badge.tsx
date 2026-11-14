"use client";

/**
 * GitStatusBadge
 *
 * Exibe o estado git do workspace ativo no header:
 *   🌿 feature-auth · ↑2 ↓0 · ●
 *
 * - Branch ativa do repositório que o Git workbench usa (o escolhido em
 *   `[git] repo`, ou o padrão do workspace; uma raiz que não é repositório,
 *   mas contém um, também aparece).
 * - Indicadores ahead/behind quando há tracking remoto.
 * - Ponto colorido: verde = limpo, laranja = alterações não commitadas.
 *
 * Polling leve a cada 5 s (pausa quando a aba está inativa).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchGitStatus, type GitStatus } from "@/components/workbench/git/api";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { m } from "@/lib/paraglide/messages";

const POLL_INTERVAL_MS = 5_000;

export function GitStatusBadge() {
  const workspaceId = useWorkspacesStore((s) => s.getActive()?.id ?? null);
  // O status vem com o workspace a que pertence: trocar de workspace esconde o
  // anterior na hora, e uma resposta atrasada do antigo não aparece no novo.
  const [snapshot, setSnapshot] = useState<{
    workspaceId: string;
    status: GitStatus;
  } | null>(null);
  const currentRef = useRef(workspaceId);
  useEffect(() => {
    currentRef.current = workspaceId;
  }, [workspaceId]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pausedRef = useRef(false);

  const refresh = useCallback(async () => {
    if (!workspaceId || pausedRef.current) return;
    try {
      const status = await fetchGitStatus(workspaceId);
      if (currentRef.current !== workspaceId) return;
      setSnapshot(status?.is_git_repo ? { workspaceId, status } : null);
    } catch {
      // silencioso — o badge some se o backend não responde
    }
  }, [workspaceId]);

  useEffect(() => {
    const schedule = () => {
      timerRef.current = setTimeout(() => {
        void refresh().finally(schedule);
      }, POLL_INTERVAL_MS);
    };

    const onVisibility = () => {
      pausedRef.current = document.hidden;
      if (!document.hidden) void refresh();
    };

    document.addEventListener("visibilitychange", onVisibility);
    // Busca inicial do status git ao montar ou trocar de workspace —
    // sincroniza com o backend, não deriva de prop.
    // oxlint-disable-next-line react/set-state-in-effect
    void refresh();
    schedule();

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [refresh]);

  if (!workspaceId || snapshot?.workspaceId !== workspaceId) return null;

  const { branch, clean, ahead, behind } = snapshot.status;
  const title = [
    m.git_badge_branch({ branch }),
    ahead > 0 ? `↑${ahead}` : "",
    behind > 0 ? `↓${behind}` : "",
    clean ? m.git_badge_clean() : m.git_badge_dirty(),
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      data-testid="git-status-badge"
      className="hidden sm:flex items-center gap-1 text-xs text-muted-foreground px-2 py-1 rounded-md hover:bg-muted/50 transition-colors cursor-default select-none"
      title={title}
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="w-3.5 h-3.5 shrink-0"
        aria-hidden="true"
      >
        <line x1="6" y1="3" x2="6" y2="15" />
        <circle cx="18" cy="6" r="3" />
        <circle cx="6" cy="18" r="3" />
        <path d="M18 9a9 9 0 0 1-9 9" />
      </svg>

      <span className="max-w-[120px] truncate font-mono">{branch}</span>
      {ahead > 0 && <span>↑{ahead}</span>}
      {behind > 0 && <span>↓{behind}</span>}
      <span
        role="img"
        aria-label={clean ? m.git_badge_clean() : m.git_badge_dirty()}
        className={`w-1.5 h-1.5 rounded-full ${clean ? "bg-green-500" : "bg-orange-400"}`}
      />
    </div>
  );
}
