"use client";

/**
 * ChangesView — aba "Mudanças" do painel Git.
 *
 * Grupos colapsáveis Staged / Modificados + painel de commit. Cada arquivo
 * tem ações inline (+/−/↩) e menu de clique direito (stage/unstage/discard).
 * O estado (resumo, arquivos abertos, hunks) vive no workbench-store (slice
 * `diff`), cacheado por workspace, revalidado por `useWorkbenchSWR`.
 */

import { ChevronDown, ChevronRight, GitCommit, Loader2 } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useWorkbenchSWR } from "@/lib/hooks/workbench/use-swr";
import {
  WORKBENCH_STALE_MS,
  useWorkbenchStore,
  type DiffFile,
  type DiffSummary,
} from "@/lib/stores/workbench-store";
import {
  apiGitCommit,
  apiGitFileAction,
  apiGitignoreAppend,
  fetchDiffFile,
} from "./api";
import { HunkView, statusTone } from "./shared";
import { useContextMenu, type ContextMenuItem } from "./git-context-menu";
import { m } from "@/lib/paraglide/messages";
import { useToastStore } from "@/lib/stores/toast-store";

function FileRow({
  workspaceId,
  file,
  onRefresh,
  onContextMenu,
}: {
  workspaceId: string;
  file: DiffFile;
  onRefresh: () => void;
  onContextMenu: (e: React.MouseEvent, file: DiffFile) => void;
}) {
  const open = useWorkbenchStore((s) =>
    s.getDiff(workspaceId).openFiles.includes(file.path),
  );
  const hunks = useWorkbenchStore(
    (s) => s.getDiff(workspaceId).hunksByFile[file.path],
  );
  const fetchedAt = useWorkbenchStore(
    (s) => s.getDiff(workspaceId).fileFetchedAt[file.path] ?? 0,
  );
  const setDiffOpenFile = useWorkbenchStore((s) => s.setDiffOpenFile);
  const setDiffHunks = useWorkbenchStore((s) => s.setDiffHunks);
  const [discardOpen, setDiscardOpen] = useState(false);

  const revalidate = useCallback(async () => {
    const h = await fetchDiffFile(workspaceId, file.path);
    if (h) setDiffHunks(workspaceId, file.path, h);
  }, [workspaceId, file.path, setDiffHunks]);

  useWorkbenchSWR({
    key: `diff:${workspaceId}:${file.path}`,
    hasCache: Array.isArray(hunks),
    isStale: () => Date.now() - fetchedAt > WORKBENCH_STALE_MS,
    revalidate,
    skip: !open,
  });

  const handleConfirmDiscard = useCallback(async () => {
    setDiscardOpen(false);
    await apiGitFileAction(workspaceId, "discard", file.path);
    onRefresh();
  }, [workspaceId, file.path, onRefresh]);

  return (
    <>
      <Dialog open={discardOpen} onOpenChange={setDiscardOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{m.workbench_git_discard_title()}</DialogTitle>
            <DialogDescription>
              {m.workbench_git_discard_body()}{" "}
              <span className="font-mono text-foreground">{file.path}</span>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <button
              onClick={() => setDiscardOpen(false)}
              className="px-3 py-1.5 text-xs rounded-md border border-border/60 hover:bg-muted/40"
            >
              {m.workbench_git_cancel()}
            </button>
            <button
              onClick={() => void handleConfirmDiscard()}
              className="px-3 py-1.5 text-xs rounded-md bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {m.workbench_git_discard_confirm()}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <div
        className="border-b border-border/40 last:border-0 group"
        onContextMenu={(e) => onContextMenu(e, file)}
      >
        <div className="flex items-center">
          <button
            onClick={() => setDiffOpenFile(workspaceId, file.path, !open)}
            className="flex-1 flex items-center gap-2 px-2 py-1.5 text-xs hover:bg-muted/30 text-left min-w-0"
          >
            {open ? (
              <ChevronDown className="w-3 h-3 shrink-0 text-muted-foreground" />
            ) : (
              <ChevronRight className="w-3 h-3 shrink-0 text-muted-foreground" />
            )}
            <span
              className={`w-3.5 text-center text-[10px] font-medium shrink-0 ${statusTone(file.status)}`}
            >
              {file.status}
            </span>
            <span className="flex-1 truncate font-mono">{file.path}</span>
            <span className="text-green-500 shrink-0">+{file.additions}</span>
            <span className="text-destructive shrink-0">−{file.deletions}</span>
          </button>
          <div className="flex items-center gap-0.5 px-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
            {file.unstaged_change || file.untracked ? (
              <button
                onClick={() =>
                  void apiGitFileAction(workspaceId, "stage", file.path).then(
                    onRefresh,
                  )
                }
                title={m.workbench_git_ctx_stage()}
                className="p-0.5 text-green-500 hover:text-green-400 text-[10px] font-bold"
                data-testid="git-stage-file-btn"
              >
                +
              </button>
            ) : null}
            {file.staged_change ? (
              <button
                onClick={() =>
                  void apiGitFileAction(workspaceId, "unstage", file.path).then(
                    onRefresh,
                  )
                }
                title={m.workbench_git_ctx_unstage()}
                className="p-0.5 text-amber-500 hover:text-amber-400 text-[10px] font-bold"
              >
                −
              </button>
            ) : null}
            {file.unstaged_change && !file.untracked ? (
              <button
                onClick={() => setDiscardOpen(true)}
                title={m.workbench_git_ctx_discard()}
                className="p-0.5 text-destructive hover:text-red-400 text-[10px]"
              >
                ↩
              </button>
            ) : null}
          </div>
        </div>
        {open && (
          <div className="px-3 pb-2 space-y-1">
            {!hunks && (
              <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />
            )}
            {hunks?.map((h, i) => (
              <HunkView key={i} hunk={h} />
            ))}
          </div>
        )}
      </div>
    </>
  );
}

function DiffGroup({
  label,
  tone,
  workspaceId,
  files,
  onRefresh,
  onContextMenu,
}: {
  label: string;
  tone: string;
  workspaceId: string;
  files: DiffFile[];
  onRefresh: () => void;
  onContextMenu: (e: React.MouseEvent, file: DiffFile) => void;
}) {
  const [open, setOpen] = useState(true);
  if (files.length === 0) return null;
  return (
    <div className="border-b border-border/40 last:border-0">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-1.5 px-2 py-1 text-[10px] font-medium uppercase tracking-wide hover:bg-muted/30 text-left"
      >
        {open ? (
          <ChevronDown className="w-3 h-3 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="w-3 h-3 shrink-0 text-muted-foreground" />
        )}
        <span className={tone}>{label}</span>
        <span className="text-muted-foreground">({files.length})</span>
      </button>
      {open &&
        files.map((f) => (
          <FileRow
            key={f.path}
            workspaceId={workspaceId}
            file={f}
            onRefresh={onRefresh}
            onContextMenu={onContextMenu}
          />
        ))}
    </div>
  );
}

export function ChangesView({
  workspaceId,
  summary,
}: {
  workspaceId: string;
  summary: DiffSummary;
}) {
  const invalidateDiff = useWorkbenchStore((s) => s.invalidateDiff);
  const showError = useCallback((message: string) => {
    useToastStore.getState().error("Git", { description: message });
  }, []);
  const menu = useContextMenu();
  const [commitMsg, setCommitMsg] = useState("");
  const [commitBody, setCommitBody] = useState("");
  const [amend, setAmend] = useState(false);
  const [committing, setCommitting] = useState(false);

  const handleRefresh = useCallback(() => {
    invalidateDiff(workspaceId);
  }, [workspaceId, invalidateDiff]);

  const handleContextMenu = useCallback(
    (e: React.MouseEvent, file: DiffFile) => {
      const items: ContextMenuItem[] = [];
      if (file.unstaged_change || file.untracked) {
        items.push({
          label: m.workbench_git_ctx_stage(),
          onSelect: () =>
            void apiGitFileAction(workspaceId, "stage", file.path).then(
              handleRefresh,
            ),
        });
      }
      if (file.staged_change) {
        items.push({
          label: m.workbench_git_ctx_unstage(),
          onSelect: () =>
            void apiGitFileAction(workspaceId, "unstage", file.path).then(
              handleRefresh,
            ),
        });
      }
      if (file.unstaged_change && !file.untracked) {
        items.push({
          label: m.workbench_git_ctx_discard(),
          danger: true,
          onSelect: () =>
            void apiGitFileAction(workspaceId, "discard", file.path).then(
              handleRefresh,
            ),
        });
      }
      items.push({
        label: "Ignore file",
        onSelect: () =>
          void apiGitignoreAppend(workspaceId, file.path).then((result) => {
            if (result.status === "error") {
              showError(result.message);
              return;
            }
            handleRefresh();
          }),
      });
      const normalizedPath = file.path.replaceAll("\\", "/");
      const parentPath = normalizedPath.split("/").slice(0, -1).join("/");
      if (file.status !== "D" && parentPath) {
        items.push({
          label: "Ignore folder",
          onSelect: () =>
            void apiGitignoreAppend(workspaceId, parentPath, true).then(
              (result) => {
                if (result.status === "error") {
                  showError(result.message);
                  return;
                }
                handleRefresh();
              },
            ),
        });
      }
      menu.open(e, items);
    },
    [workspaceId, handleRefresh, menu, showError],
  );

  const handleCommit = async () => {
    if (!commitMsg.trim()) return;
    setCommitting(true);
    try {
      const result = await apiGitCommit(workspaceId, commitMsg.trim(), false, {
        body: commitBody.trim(),
        amend,
      });
      if (result.status === "ok") {
        setCommitMsg("");
        setCommitBody("");
        setAmend(false);
        handleRefresh();
      }
    } finally {
      setCommitting(false);
    }
  };

  const { staged, unstaged, untracked } = useMemo(() => {
    const stagedFiles: DiffFile[] = [];
    const unstagedFiles: DiffFile[] = [];
    const untrackedFiles: DiffFile[] = [];
    for (const f of summary.files) {
      if (f.staged_change) stagedFiles.push(f);
      if (f.unstaged_change) unstagedFiles.push(f);
      if (f.untracked) untrackedFiles.push(f);
    }
    return {
      staged: stagedFiles,
      unstaged: unstagedFiles,
      untracked: untrackedFiles,
    };
  }, [summary.files]);

  if (summary.files.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-2 p-4 text-center">
        <p className="text-xs text-muted-foreground">
          {m.workbench_diff_clean()}
        </p>
        <p className="text-[10px] text-muted-foreground/60">
          {m.workbench_diff_clean_hint()}
        </p>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {menu.element}
      <div className="px-2 py-1.5 border-b border-border/60 flex items-center justify-between">
        <span className="text-xs text-muted-foreground">
          {m.workbench_diff_files_badge({ n: summary.files.length })}
        </span>
        <span className="text-xs font-mono">
          <span className="text-green-500">+{summary.total_additions}</span>{" "}
          <span className="text-destructive">−{summary.total_deletions}</span>
        </span>
      </div>
      <div className="flex-1 overflow-y-auto min-h-0">
        <DiffGroup
          label={m.workbench_diff_group_staged()}
          tone="text-green-500"
          workspaceId={workspaceId}
          files={staged}
          onRefresh={handleRefresh}
          onContextMenu={handleContextMenu}
        />
        <DiffGroup
          label={m.workbench_diff_group_unstaged()}
          tone="text-amber-500"
          workspaceId={workspaceId}
          files={unstaged}
          onRefresh={handleRefresh}
          onContextMenu={handleContextMenu}
        />
        {untracked.length > 0 && (
          <DiffGroup
            label={m.workbench_diff_group_untracked()}
            tone="text-blue-500"
            workspaceId={workspaceId}
            files={untracked}
            onRefresh={handleRefresh}
            onContextMenu={handleContextMenu}
          />
        )}
      </div>
      <div className="border-t border-border/60 p-2 flex flex-col gap-1.5 bg-muted/10 shrink-0">
        <input
          type="text"
          value={commitMsg}
          onChange={(e) => setCommitMsg(e.target.value)}
          placeholder={m.workbench_diff_commit_placeholder()}
          className="w-full rounded-md border border-border/60 bg-background px-2 py-1 text-xs font-mono placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-ring"
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              void handleCommit();
            }
          }}
          data-testid="git-commit-message"
        />
        <textarea
          value={commitBody}
          onChange={(e) => setCommitBody(e.target.value)}
          placeholder={m.workbench_diff_commit_body_placeholder()}
          rows={2}
          className="w-full resize-none rounded-md border border-border/60 bg-background px-2 py-1 text-xs font-mono placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-ring"
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              void handleCommit();
            }
          }}
          data-testid="git-commit-body"
        />
        <label className="flex items-center gap-1.5 text-[10px] text-muted-foreground select-none">
          <input
            type="checkbox"
            checked={amend}
            onChange={(e) => setAmend(e.target.checked)}
            data-testid="git-commit-amend"
          />
          {m.workbench_diff_commit_amend_label()}
        </label>
        <button
          onClick={() => void handleCommit()}
          disabled={!commitMsg.trim() || committing}
          className="flex items-center justify-center gap-1.5 py-1 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          data-testid="git-commit-btn"
        >
          {committing ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <GitCommit className="w-3 h-3" />
          )}
          {m.workbench_diff_commit_button()}
        </button>
      </div>
    </div>
  );
}
