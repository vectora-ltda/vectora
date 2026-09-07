"use client";

/**
 * GitTab — painel Git do workbench.
 *
 * Estrutura: toolbar (branch · sync · PR) + 2 abas (Mudanças | Histórico).
 * Comparar/merge entra como overlay de tela cheia; stash, worktrees e criação
 * de PR são modais.
 */

import { GitBranch, Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useWorkbenchSWR } from "@/lib/hooks/workbench/use-swr";
import { useDelayedLoading } from "@/lib/hooks/use-delayed-loading";
import {
  WORKBENCH_STALE_MS,
  useWorkbenchStore,
} from "@/lib/stores/workbench-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { useCIStore } from "@/lib/stores/ci-store";
import { DiffSkeleton } from "../tabs/diff-skeleton";
import {
  apiCreatePR,
  fetchBranches,
  fetchDiff,
  fetchGitStatus,
  fetchGitOperation,
  fetchPullRequests,
  type GitBranches,
  type GitStatus,
  type PullRequest,
} from "./api";
import { GitToolbar } from "./git-toolbar";
import { ChangesView } from "./changes-view";
import { HistoryView } from "./history-view";
import { CompareView } from "./compare-view";
import { StashModal } from "./stash-modal";
import { WorktreesModal } from "./worktrees-modal";
import { m } from "@/lib/paraglide/messages";

type GitView = "changes" | "history";

function PrDialog({
  workspaceId,
  head,
  open,
  onOpenChange,
}: {
  workspaceId: string;
  head: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [prs, setPrs] = useState<PullRequest[]>([]);
  const [available, setAvailable] = useState(true);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [baseBranch, setBaseBranch] = useState("main");
  const [submitting, setSubmitting] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!open) return;
    void fetchPullRequests(workspaceId).then((r) => {
      setAvailable(r.available);
      setPrs(r.prs);
    });
  }, [open, workspaceId]);

  const handleCreate = async () => {
    if (!title.trim()) return;
    setSubmitting(true);
    setMsg("");
    try {
      const r = await apiCreatePR(
        workspaceId,
        title.trim(),
        body,
        baseBranch.trim(),
      );
      setMsg(r.status === "ok" ? m.workbench_git_pr_created() : r.message);
      if (r.status === "ok") {
        setTitle("");
        setBody("");
        void fetchPullRequests(workspaceId).then((res) => setPrs(res.prs));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{m.workbench_git_pr()}</DialogTitle>
        </DialogHeader>
        {!available ? (
          <p className="text-xs text-muted-foreground">
            {m.workbench_git_pr_unavailable()}
          </p>
        ) : (
          <>
            <div className="max-h-40 overflow-y-auto -mx-1">
              {prs.length === 0 ? (
                <p className="text-xs text-muted-foreground py-2 px-1">
                  {m.workbench_git_pr_empty()}
                </p>
              ) : (
                prs.map((pr) => (
                  <div key={pr.number} className="px-1 py-1 text-xs">
                    <span className="font-mono text-primary">#{pr.number}</span>{" "}
                    <span className="text-foreground/90">{pr.title}</span>
                    <span className="text-[10px] text-muted-foreground font-mono ml-1">
                      {pr.head} → {pr.base}
                    </span>
                  </div>
                ))
              )}
            </div>
            <div className="flex flex-col gap-1.5 border-t border-border/60 pt-2">
              <p className="text-[10px] text-muted-foreground font-mono">
                {head}
              </p>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={m.workbench_git_pr_title_placeholder()}
                className="text-xs bg-background border border-border/60 rounded px-2 py-1 outline-none focus:border-primary"
              />
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                placeholder={m.workbench_git_pr_body_placeholder()}
                rows={2}
                className="text-xs bg-background border border-border/60 rounded px-2 py-1 outline-none focus:border-primary resize-none"
              />
              <input
                value={baseBranch}
                onChange={(e) => setBaseBranch(e.target.value)}
                placeholder={m.workbench_git_pr_base_placeholder()}
                className="text-xs font-mono bg-background border border-border/60 rounded px-2 py-1 outline-none focus:border-primary"
              />
              {msg && (
                <p className="text-[10px] text-muted-foreground">{msg}</p>
              )}
              <button
                onClick={() => void handleCreate()}
                disabled={!title.trim() || submitting}
                className="flex items-center justify-center gap-1.5 py-1 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                {submitting && <Loader2 className="w-3 h-3 animate-spin" />}
                {m.workbench_git_pr_submit()}
              </button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function GitTab(_props: { threadId: string }) {
  const workspace = useWorkspacesStore((s) => s.getActive());
  const wsId = workspace?.id ?? "";
  const lastCi = useCIStore((s) => s.lastRun);

  const summary = useWorkbenchStore((s) => s.getDiff(wsId).summary);
  const fetchedAt = useWorkbenchStore((s) => s.getDiff(wsId).summaryFetchedAt);
  const setDiffSummary = useWorkbenchStore((s) => s.setDiffSummary);
  const invalidateDiff = useWorkbenchStore((s) => s.invalidateDiff);
  const clearPending = useWorkbenchStore((s) => s.clearPending);
  const setGitOperation = useWorkbenchStore((s) => s.setGitOperation);

  const [view, setView] = useState<GitView>("changes");
  const [compareOpen, setCompareOpen] = useState(false);
  const [stashOpen, setStashOpen] = useState(false);
  const [worktreesOpen, setWorktreesOpen] = useState(false);
  const [prOpen, setPrOpen] = useState(false);
  const [prHead, setPrHead] = useState("");
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [branches, setBranches] = useState<GitBranches | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Diff summary via SWR (mesmo padrão do antigo DiffTab).
  useEffect(() => {
    // fetchedAt dispara a limpeza do pending sempre que um novo fetch chega.
    if (wsId && fetchedAt) clearPending(wsId, "diff");
  }, [wsId, fetchedAt, clearPending]);

  useWorkbenchSWR({
    key: `diff-summary:${wsId}`,
    hasCache: summary !== null,
    isStale: () => Date.now() - fetchedAt > WORKBENCH_STALE_MS,
    revalidate: async () => {
      if (!wsId) return;
      const data = await fetchDiff(wsId);
      if (data) setDiffSummary(wsId, data);
    },
    skip: !wsId,
  });

  // Status + branches (alimentam a toolbar); refreshKey força nova busca
  // após ações git (checkout, commit, etc.) via handleChanged.
  useEffect(() => {
    if (!wsId) return;
    if (refreshKey >= 0) {
      void fetchGitStatus(wsId).then(setStatus);
      void fetchBranches(wsId).then(setBranches);
    }
  }, [wsId, refreshKey]);

  // Reconcile the backend operation snapshot while a Git command is running.
  // The polling is deliberately scoped to the active workspace so switching
  // workspaces cannot leak progress or errors into another repository.
  useEffect(() => {
    if (!wsId) return;
    let cancelled = false;
    let requestInFlight = false;
    let requestGeneration = 0;
    const refreshOperation = async () => {
      if (requestInFlight) return;
      requestInFlight = true;
      const generation = ++requestGeneration;
      try {
        const operation = await fetchGitOperation(wsId);
        if (cancelled || generation !== requestGeneration || !operation) return;
        setGitOperation(wsId, {
          operationId: operation.operation_id,
          operation: operation.operation,
          state: operation.state,
          phase: operation.phase,
          progress: operation.progress,
          errorCode: operation.error_code,
          error: operation.error,
          updatedAt: (operation.finished_at ?? operation.created_at) * 1000,
        });
      } catch {
        // Preserve the last valid snapshot while the backend is unavailable.
      } finally {
        requestInFlight = false;
      }
    };
    void refreshOperation();
    const timer = window.setInterval(() => void refreshOperation(), 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [wsId, setGitOperation]);

  const handleChanged = useCallback(() => {
    if (wsId) invalidateDiff(wsId);
    setRefreshKey((k) => k + 1);
  }, [wsId, invalidateDiff]);

  const handleOpenPR = useCallback((head: string) => {
    setPrHead(head);
    setPrOpen(true);
  }, []);

  const showSkeleton = useDelayedLoading(summary === null && Boolean(wsId));

  if (!workspace) {
    return (
      <div className="h-full flex items-center justify-center text-xs text-muted-foreground p-4 text-center">
        {m.workbench_diff_no_workspace()}
      </div>
    );
  }
  if (!summary) {
    return showSkeleton ? <DiffSkeleton /> : <div className="h-full" />;
  }
  if (!summary.is_git_repo) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-2 p-4 text-center">
        <GitBranch className="w-6 h-6 text-muted-foreground" />
        <p className="text-xs text-muted-foreground">
          {m.workbench_diff_not_git()}
        </p>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <GitToolbar
        workspaceId={wsId}
        status={status}
        branches={branches}
        onCompare={() => setCompareOpen(true)}
        onOpenStash={() => setStashOpen(true)}
        onOpenWorktrees={() => setWorktreesOpen(true)}
        onOpenPR={handleOpenPR}
        onChanged={handleChanged}
      />

      {lastCi && (
        <a
          href={lastCi.htmlUrl || undefined}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1.5 px-3 py-1 shrink-0 border-b border-border/60 text-[11px] text-muted-foreground hover:bg-muted/40 transition-colors"
          data-testid="git-ci-badge"
        >
          <span
            className={`w-2 h-2 rounded-full ${
              lastCi.status !== "completed"
                ? "bg-amber-500 animate-pulse"
                : lastCi.conclusion === "success"
                  ? "bg-emerald-500"
                  : "bg-red-500"
            }`}
          />
          <span className="font-medium text-foreground/80">
            {lastCi.status !== "completed"
              ? m.workbench_ci_running()
              : lastCi.conclusion === "success"
                ? m.workbench_ci_passed()
                : m.workbench_ci_failed()}
          </span>
          <span className="truncate">{lastCi.name}</span>
        </a>
      )}

      {compareOpen ? (
        <div className="flex-1 min-h-0">
          <CompareView
            workspaceId={wsId}
            branches={branches?.branches ?? []}
            current={status?.branch || branches?.current || ""}
            onBack={() => setCompareOpen(false)}
            onChanged={handleChanged}
            onOpenPR={handleOpenPR}
          />
        </div>
      ) : (
        <>
          {/* Barra de abas: só Mudanças | Histórico */}
          <div className="flex shrink-0 border-b border-border/60">
            <button
              onClick={() => setView("changes")}
              aria-pressed={view === "changes"}
              className={`px-3 py-1.5 text-xs font-medium transition-colors ${
                view === "changes"
                  ? "border-b-2 border-primary text-foreground -mb-px"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {m.workbench_diff_tab_changes()}
            </button>
            <button
              onClick={() => setView("history")}
              aria-pressed={view === "history"}
              className={`px-3 py-1.5 text-xs font-medium transition-colors ${
                view === "history"
                  ? "border-b-2 border-primary text-foreground -mb-px"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {m.workbench_git_tab_history()}
            </button>
          </div>
          <div className="flex-1 min-h-0">
            {view === "changes" ? (
              <ChangesView workspaceId={wsId} summary={summary} />
            ) : (
              <HistoryView workspaceId={wsId} onChanged={handleChanged} />
            )}
          </div>
        </>
      )}

      <StashModal
        workspaceId={wsId}
        open={stashOpen}
        onOpenChange={setStashOpen}
        onChanged={handleChanged}
      />
      <WorktreesModal
        workspaceId={wsId}
        open={worktreesOpen}
        onOpenChange={setWorktreesOpen}
      />
      <PrDialog
        workspaceId={wsId}
        head={prHead}
        open={prOpen}
        onOpenChange={setPrOpen}
      />
    </div>
  );
}
