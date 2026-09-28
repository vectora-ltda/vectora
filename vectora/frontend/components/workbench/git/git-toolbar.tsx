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
  ChevronDown,
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
import type { GitOpsSnapshot } from "@/lib/stores/workbench-store";
import { m } from "@/lib/paraglide/messages";
import { useSettingsStore } from "@/lib/stores/settings-store";

export function GitToolbar({
  workspaceId,
  status,
  branches,
  onCompare,
  onOpenStash,
  onOpenWorktrees,
  onOpenPR,
  onChanged,
  operation,
}: {
  workspaceId: string;
  status: GitStatus | null;
  branches: GitBranches | null;
  onCompare: () => void;
  onOpenStash: () => void;
  onOpenWorktrees: () => void;
  onOpenPR: (head: string) => void;
  onChanged: () => void;
  operation?: GitOpsSnapshot | null;
}) {
  const [creating, setCreating] = useState(false);
  const [newBranch, setNewBranch] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncMenuOpen, setSyncMenuOpen] = useState(false);
  const gitBypassEnabled = useSettingsStore((s) => s.gitBypassEnabled);
  const operationActive =
    operation?.state === "queued" || operation?.state === "running";
  const hasConflict = (status?.ahead ?? 0) > 0 && (status?.behind ?? 0) > 0;

  const current = status?.branch || branches?.current || "—";
  const others = (branches?.branches ?? []).filter((b) => b !== current);
  const primaryAction =
    (status?.behind ?? 0) > 0
      ? {
          action: "pull" as const,
          label: m.workbench_git_sync_pull({ n: status?.behind ?? 0 }),
          icon: ArrowDown,
        }
      : (status?.ahead ?? 0) > 0
        ? {
            action: "push" as const,
            label: m.workbench_git_sync_push({ n: status?.ahead ?? 0 }),
            icon: ArrowUp,
          }
        : {
            action: "fetch" as const,
            label: m.workbench_git_sync_fetch(),
            icon: RefreshCw,
          };

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

  const handleSync = async (
    action: "fetch" | "pull" | "push",
    force = false,
  ) => {
    if (syncing || operationActive) return;
    if (force) {
      if (!gitBypassEnabled) return;
      if (!window.confirm(m.workbench_git_force_push_warning())) return;
    }
    setSyncing(true);
    try {
      if (force) await apiSync(workspaceId, action, { force: true });
      else await apiSync(workspaceId, action);
      onChanged();
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="min-w-0 shrink-0 border-b border-border/60">
      <div className="flex h-11 items-center gap-1.5 px-3 py-1.5">
        {/* Branch dropdown */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              className="flex min-w-0 max-w-[55%] items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs hover:bg-muted/50 transition-colors"
              title={m.tooltip_git_branch()}
              aria-label={m.tooltip_git_branch()}
            >
              <GitBranch className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate font-mono">{current}</span>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="start"
            className="flex max-h-[min(80vh,28rem)] min-w-[200px] flex-col"
          >
            <div className="shrink-0">
              <DropdownMenuLabel>
                {m.workbench_git_branch_menu()}
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
            </div>
            <div
              className="min-h-0 flex-1 overflow-y-auto"
              data-testid="git-branch-list"
            >
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
            </div>
            <div className="shrink-0">
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
            </div>
          </DropdownMenuContent>
        </DropdownMenu>

        <div className="flex-1" />

        <div className="relative inline-flex shrink-0 items-stretch">
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={() => void handleSync(primaryAction.action)}
                disabled={syncing || operationActive}
                aria-label={primaryAction.label}
                className="flex h-8 min-w-0 items-center gap-1.5 rounded-l-md border border-border/60 px-2.5 text-xs hover:bg-muted/50 disabled:opacity-50 transition-colors"
              >
                {syncing ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <primaryAction.icon className="w-3.5 h-3.5 text-muted-foreground" />
                )}
                <span className="hidden sm:inline">{primaryAction.label}</span>
                {primaryAction.action !== "fetch" && (
                  <span className="rounded bg-muted px-1 font-mono text-[10px]">
                    {primaryAction.action === "pull"
                      ? `${status?.behind ?? 0}`
                      : `${status?.ahead ?? 0}`}
                  </span>
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{primaryAction.label}</TooltipContent>
          </Tooltip>
          <button
            type="button"
            aria-label={m.workbench_git_sync_menu()}
            onClick={() => setSyncMenuOpen((v) => !v)}
            disabled={syncing || operationActive}
            className="flex h-8 w-7 items-center justify-center rounded-r-md border border-l-0 border-border/60 px-1.5 text-xs hover:bg-muted/50 disabled:opacity-50"
          >
            <ChevronDown className="h-3.5 w-3.5" />
          </button>
          {syncMenuOpen && (
            <div
              role="menu"
              className="absolute right-0 top-full z-20 mt-1 w-56 rounded-md border border-border bg-popover p-1 shadow-lg"
            >
              <button
                role="menuitem"
                disabled={syncing || operationActive}
                className="flex w-full items-start gap-2 rounded px-2 py-2 text-left text-xs hover:bg-muted"
                onClick={() => {
                  setSyncMenuOpen(false);
                  void handleSync("fetch");
                }}
              >
                <RefreshCw className="mt-0.5 h-3.5 w-3.5" />
                <span>
                  <b>{m.workbench_git_sync_fetch()}</b>
                  <small className="block text-muted-foreground">
                    {m.workbench_git_sync_fetch_description()}
                  </small>
                </span>
              </button>
              {hasConflict && (
                <button
                  role="menuitem"
                  disabled={syncing || operationActive}
                  className="flex w-full items-start gap-2 rounded px-2 py-2 text-left text-xs hover:bg-muted"
                  onClick={() => {
                    setSyncMenuOpen(false);
                    void handleSync("push", true);
                  }}
                >
                  <ArrowUp className="mt-0.5 h-3.5 w-3.5" />
                  <span>
                    <b>{m.workbench_git_force_push()}</b>
                    <small className="block text-git-warning">
                      {m.workbench_git_force_push_warning()}
                    </small>
                  </span>
                </button>
              )}
            </div>
          )}
        </div>

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
      {operation &&
        (operation.state === "queued" || operation.state === "running") && (
          <div
            className="flex items-center gap-2 border-t border-border/40 px-3 py-1 text-[10px] text-muted-foreground"
            role="status"
          >
            <Loader2 className="h-3 w-3 animate-spin text-primary" />
            <span className="truncate">
              {m.workbench_git_operation_running({
                operation: operation.operation,
                phase: operation.phase,
              })}
            </span>
            <span className="ml-auto font-mono">
              {Math.round(operation.progress)}%
            </span>
          </div>
        )}
      {operation &&
        (operation.state === "succeeded" || operation.state === "failed") && (
          <div
            className={`flex items-center gap-2 border-t border-border/40 px-3 py-1 text-[10px] ${operation.state === "failed" ? "text-destructive" : "text-git-success"}`}
            role="status"
          >
            <span aria-hidden="true">
              {operation.state === "failed" ? "!" : "✓"}
            </span>
            <span className="truncate">
              {operation.state === "failed"
                ? m.workbench_git_operation_failed()
                : m.workbench_git_operation_succeeded()}
            </span>
            {operation.error && (
              <span className="truncate text-muted-foreground">
                {operation.error}
              </span>
            )}
          </div>
        )}

      {/* Linha de criação de branch (inline, aparece sob demanda) */}
      {creating && (
        <div className="flex items-center gap-1.5 px-3 pb-2">
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
