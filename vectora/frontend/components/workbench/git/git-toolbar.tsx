"use client";

/**
 * GitToolbar — barra de ações do painel Git (estilo GitHub Desktop).
 *
 * - Branch (dropdown): branch atual + trocar / criar / comparar-merge /
 *   worktrees.
 * - Sync (botão adaptativo): Pull N · Push N · Fetch conforme ahead/behind.
 * - Pull requests (dropdown): lista abertos + criar PR.
 *
 * Tudo o que antes eram 6 sub-abas de texto cabe aqui em ícones/dropdowns,
 * eliminando o overflow horizontal da barra antiga.
 */

import {
  ArrowDown,
  ArrowUp,
  GitBranch,
  GitPullRequest,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { useCallback, useState } from "react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { apiCheckout, apiSync, type GitBranches, type GitStatus } from "./api";
import { m } from "@/lib/paraglide/messages";

export function GitToolbar({
  workspaceId,
  status,
  branches,
  onCompare,
  onOpenStash,
  onOpenWorktrees,
  onOpenPR,
  onChanged,
}: {
  workspaceId: string;
  status: GitStatus | null;
  branches: GitBranches | null;
  onCompare: () => void;
  onOpenStash: () => void;
  onOpenWorktrees: () => void;
  onOpenPR: (head: string) => void;
  onChanged: () => void;
}) {
  const [creating, setCreating] = useState(false);
  const [newBranch, setNewBranch] = useState("");
  const [syncing, setSyncing] = useState(false);

  const current = status?.branch || branches?.current || "—";
  const others = (branches?.branches ?? []).filter((b) => b !== current);
  const syncActions = [
    {
      action: "fetch" as const,
      label: m.workbench_git_sync_fetch(),
      icon: RefreshCw,
    },
    {
      action: "pull" as const,
      label: m.workbench_git_sync_pull({ n: status?.behind ?? 0 }),
      icon: ArrowDown,
    },
    {
      action: "push" as const,
      label: m.workbench_git_sync_push({ n: status?.ahead ?? 0 }),
      icon: ArrowUp,
    },
  ];

  const handleCheckout = useCallback(
    async (ref: string, create = false) => {
      await apiCheckout(workspaceId, ref, create);
      onChanged();
    },
    [workspaceId, onChanged],
  );

  const handleCreate = useCallback(async () => {
    if (!newBranch.trim()) return;
    await handleCheckout(newBranch.trim(), true);
    setNewBranch("");
    setCreating(false);
  }, [newBranch, handleCheckout]);

  const handleSync = async (action: "fetch" | "pull" | "push") => {
    setSyncing(true);
    try {
      await apiSync(workspaceId, action);
      onChanged();
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="shrink-0 border-b border-border/60">
      <div className="flex items-center gap-1 px-2 py-1.5">
        {/* Branch dropdown */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              className="flex items-center gap-1.5 min-w-0 max-w-[55%] px-2 py-1 rounded-md text-xs hover:bg-muted/50 transition-colors"
              title={m.tooltip_git_branch()}
              aria-label={m.tooltip_git_branch()}
            >
              <GitBranch className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate font-mono">{current}</span>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="min-w-[200px]">
            <DropdownMenuLabel>
              {m.workbench_git_branch_menu()}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            {others.length === 0 ? (
              <DropdownMenuItem disabled>
                {m.workbench_git_branch_empty()}
              </DropdownMenuItem>
            ) : (
              others.map((b) => (
                <DropdownMenuItem
                  key={b}
                  onSelect={() => void handleCheckout(b)}
                  className="font-mono text-xs"
                >
                  {b}
                </DropdownMenuItem>
              ))
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => setCreating(true)}>
              {m.workbench_git_branch_create()}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onCompare}>
              {m.workbench_git_branch_compare()}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onOpenWorktrees}>
              {m.workbench_git_branch_worktrees()}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onOpenStash}>
              {m.workbench_git_stash_view()}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        <div className="flex-1" />

        {/* Fetch, Pull e Push permanecem visíveis como ações independentes. */}
        {syncActions.map(({ action, label, icon: Icon }) => (
          <Tooltip key={action}>
            <TooltipTrigger asChild>
              <button
                onClick={() => void handleSync(action)}
                disabled={syncing}
                aria-label={label}
                className="flex items-center gap-1 px-2 py-1 rounded-md text-xs hover:bg-muted/50 disabled:opacity-50 transition-colors shrink-0"
              >
                {syncing ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Icon className="w-3.5 h-3.5 text-muted-foreground" />
                )}
                <span className="hidden sm:inline">{label}</span>
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{label}</TooltipContent>
          </Tooltip>
        ))}

        {/* PR */}
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={() => onOpenPR(current)}
              aria-label={m.tooltip_git_pr()}
              className="flex items-center justify-center w-7 h-7 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors shrink-0"
            >
              <GitPullRequest className="w-3.5 h-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">{m.tooltip_git_pr()}</TooltipContent>
        </Tooltip>
      </div>

      {/* Linha de criação de branch (inline, aparece sob demanda) */}
      {creating && (
        <div className="flex items-center gap-1.5 px-2 pb-1.5">
          <input
            autoFocus
            value={newBranch}
            onChange={(e) => setNewBranch(e.target.value)}
            placeholder={m.workbench_git_branch_create_placeholder()}
            className="flex-1 text-xs font-mono bg-background border border-border/60 rounded px-1.5 py-0.5 outline-none focus:border-primary min-w-0"
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleCreate();
              if (e.key === "Escape") setCreating(false);
            }}
          />
          <button
            onClick={() => void handleCreate()}
            className="text-[10px] px-2 py-0.5 rounded bg-primary/10 text-primary hover:bg-primary/20"
          >
            {m.workbench_git_branch_create().replace("…", "")}
          </button>
        </div>
      )}
    </div>
  );
}
