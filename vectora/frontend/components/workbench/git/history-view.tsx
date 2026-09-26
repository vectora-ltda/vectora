"use client";

/* oxlint-disable react(set-state-in-effect) */

/** Histórico Git em duas colunas: commits à esquerda e detalhes do commit à direita. */

import { Code2, FileCode2, Loader2, Search } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { lazy, Suspense } from "react";

import {
  apiCheckout,
  apiCherryPick,
  apiReorder,
  apiRevert,
  apiSquash,
  fetchCommitDiff,
  fetchGitLog,
  type GitLogCommit,
} from "./api";
import { useContextMenu, type ContextMenuItem } from "./git-context-menu";
import { m } from "@/lib/paraglide/messages";
import { useIsDark } from "@/lib/hooks/use-is-dark";

const MonacoReadOnly = lazy(
  () => import("@/components/workbench/monaco-readonly"),
);

type DiffFile = { path: string; lines: string[] };

export interface GitCommitDetailsState {
  commit: GitLogCommit;
  diff: string | null;
  loading: boolean;
  error?: string;
}

function formatDate(raw: string): string {
  try {
    const d = new Date(raw);
    return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`;
  } catch {
    return raw.slice(0, 10);
  }
}

function parseDiff(diff: string): DiffFile[] {
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;
  for (const line of diff.split("\n")) {
    const match = line.match(/^diff --git a\/(.+) b\/(.+)$/);
    if (match) {
      current = { path: match[2], lines: [line] };
      files.push(current);
    } else if (current) {
      current.lines.push(line);
    }
  }
  return files;
}

export function EmptyCommitDetails() {
  return (
    <div className="flex h-full min-h-56 flex-col items-center justify-center gap-3 p-6 text-center">
      <Code2 className="h-10 w-10 text-muted-foreground/60" />
      <p className="text-sm text-muted-foreground">
        {m.workbench_git_commit_details()}
      </p>
      <p className="text-xs text-muted-foreground/70">
        {m.workbench_git_commit_details_hint()}
      </p>
    </div>
  );
}

export function CommitDetails({
  commit,
  diff,
  loading,
  error,
}: {
  commit: GitLogCommit;
  diff: string | null;
  loading: boolean;
  error?: string;
}) {
  const files = useMemo(() => parseDiff(diff ?? ""), [diff]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [filesWidth, setFilesWidth] = useState(200);
  const [headerHeight, setHeaderHeight] = useState(168);
  const resizeRef = useRef<{
    kind: "files" | "header";
    start: number;
    value: number;
  } | null>(null);

  const selected = files.find((file) => file.path === selectedPath) ?? files[0];
  const isDark = useIsDark();

  const stopResize = useCallback(() => {
    resizeRef.current = null;
    document.body.style.removeProperty("cursor");
    document.body.style.removeProperty("user-select");
  }, []);

  useEffect(() => {
    const handlePointerMove = (event: globalThis.PointerEvent) => {
      const resize = resizeRef.current;
      if (!resize) return;
      if (resize.kind === "files") {
        setFilesWidth(
          Math.min(
            420,
            Math.max(160, resize.value + event.clientX - resize.start),
          ),
        );
      } else {
        setHeaderHeight(
          Math.min(
            360,
            Math.max(132, resize.value + event.clientY - resize.start),
          ),
        );
      }
    };
    const handlePointerUp = () => stopResize();
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      stopResize();
    };
  }, [stopResize]);

  const beginResize = useCallback(
    (kind: "files" | "header", event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      resizeRef.current = {
        kind,
        start: kind === "files" ? event.clientX : event.clientY,
        value: kind === "files" ? filesWidth : headerHeight,
      };
      document.body.style.cursor =
        kind === "files" ? "col-resize" : "row-resize";
      document.body.style.userSelect = "none";
    },
    [filesWidth, headerHeight],
  );

  const adjustResize = useCallback(
    (kind: "files" | "header", delta: number) => {
      if (kind === "files") {
        setFilesWidth((value) => Math.min(420, Math.max(160, value + delta)));
      } else {
        setHeaderHeight((value) => Math.min(360, Math.max(132, value + delta)));
      }
    },
    [],
  );

  const handleResizeKeyDown = useCallback(
    (kind: "files" | "header", event: KeyboardEvent<HTMLDivElement>) => {
      const delta =
        event.key === "ArrowRight" || event.key === "ArrowDown"
          ? 8
          : event.key === "ArrowLeft" || event.key === "ArrowUp"
            ? -8
            : 0;
      if (!delta) return;
      event.preventDefault();
      adjustResize(kind, delta);
    },
    [adjustResize],
  );

  return (
    <div className="flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden bg-[#1f1f1f]">
      <div
        style={{ minHeight: headerHeight, maxHeight: 360 }}
        className="flex shrink-0 flex-col gap-2 overflow-hidden border-b border-border/60 bg-[#202020] px-3 py-3"
      >
        <h2 className="self-stretch break-words text-sm font-semibold leading-5 text-foreground">
          {commit.message}
        </h2>
        {commit.body && (
          <pre
            style={{ fontFamily: '"Segoe UI", ui-sans-serif, sans-serif' }}
            className="max-h-40 min-h-0 self-stretch overflow-auto whitespace-pre-wrap rounded-sm bg-[#2f2f2f] px-2 py-1 text-xs leading-4 text-foreground"
          >
            {commit.body}
          </pre>
        )}
        <div className="flex w-fit max-w-full flex-wrap items-center gap-x-3 gap-y-1 text-xs font-normal leading-4 text-muted-foreground">
          <span className="whitespace-nowrap">
            {commit.author.split("<")[0].trim()}
          </span>
          <span
            className="whitespace-nowrap"
            style={{ fontFamily: "var(--font-aeonik-mono)" }}
          >
            {commit.sha_short}
          </span>
          <span className="whitespace-nowrap">{formatDate(commit.date)}</span>
        </div>
      </div>
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-valuenow={headerHeight}
        tabIndex={0}
        onPointerDown={(event) => beginResize("header", event)}
        onKeyDown={(event) => handleResizeKeyDown("header", event)}
        className="h-1 shrink-0 cursor-row-resize bg-border/40 transition-colors hover:bg-primary/60 focus:bg-primary/60"
      />
      {loading ? (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : error ? (
        <div className="flex flex-1 items-center justify-center p-6 text-sm text-destructive">
          {error}
        </div>
      ) : (
        <div className="flex min-h-0 min-w-0 flex-1">
          <div
            style={{ width: filesWidth }}
            className="shrink-0 min-w-0 overflow-y-auto vectora-no-scrollbar-gutter bg-[#202020]"
          >
            <div className="flex h-7 items-center justify-center border-b border-border/60 bg-[#2a2a2a] px-2 text-xs text-muted-foreground">
              {m.workbench_diff_summary({ n: files.length })}
            </div>
            {files.length === 0 ? (
              <p className="p-3 text-xs text-muted-foreground">
                {m.workbench_diff_clean()}
              </p>
            ) : (
              files.map((file) => (
                <button
                  key={file.path}
                  type="button"
                  onClick={() => setSelectedPath(file.path)}
                  className={`flex h-7 w-full items-center gap-1 border-b border-border/40 px-2 text-left text-xs transition-colors hover:bg-muted/40 ${selected?.path === file.path ? "bg-[#3f3f3f] text-foreground" : "text-muted-foreground"}`}
                >
                  <FileCode2 className="h-3 w-3 shrink-0 text-git-modification" />
                  <span className="truncate font-mono text-xs leading-4">
                    {file.path}
                  </span>
                </button>
              ))
            )}
          </div>
          <div
            role="separator"
            aria-orientation="vertical"
            aria-valuenow={filesWidth}
            tabIndex={0}
            onPointerDown={(event) => beginResize("files", event)}
            onKeyDown={(event) => handleResizeKeyDown("files", event)}
            className="w-px shrink-0 cursor-col-resize bg-border/60 transition-colors hover:bg-primary/60 focus:bg-primary/60"
          />
          <div className="min-w-0 flex-1 overflow-hidden bg-background/30">
            {selected ? (
              <div className="flex h-full min-h-0 flex-col">
                <div className="sticky top-0 z-10 border-b border-border/60 bg-muted/70 px-3 py-2 font-mono text-xs text-foreground">
                  {selected.path}
                </div>
                <div className="min-h-0 flex-1">
                  <Suspense
                    fallback={
                      <div className="p-3 text-xs text-muted-foreground">
                        {m.workbench_git_diff_view()}…
                      </div>
                    }
                  >
                    <MonacoReadOnly
                      key={selected.path}
                      value={selected.lines.join("\n")}
                      path={`${selected.path}.diff`}
                      isDark={isDark}
                      diffColors
                    />
                  </Suspense>
                </div>
              </div>
            ) : (
              <EmptyCommitDetails />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function HistoryView({
  workspaceId,
  onChanged,
  onOpenCommitDetails,
}: {
  workspaceId: string;
  onChanged: () => void;
  onOpenCommitDetails?: (details: GitCommitDetailsState) => void;
}) {
  const menu = useContextMenu();
  const [data, setData] = useState<{
    branch: string;
    commits: GitLogCommit[];
    has_more: boolean;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [search, setSearch] = useState("");
  const [selectedCommit, setSelectedCommit] = useState<GitLogCommit | null>(
    null,
  );
  const [selectedDiff, setSelectedDiff] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [squashBase, setSquashBase] = useState<string | null>(null);
  const detailsRequest = useRef(0);
  const logRequest = useRef(0);
  const workspaceRequest = useRef(0);
  const updateLoading = useCallback((value: boolean) => setLoading(value), []);
  const resetSelection = useCallback(() => {
    setSelectedCommit(null);
    setSelectedDiff(null);
  }, []);

  useEffect(() => {
    if (!workspaceId) return;
    const epoch = ++workspaceRequest.current;
    const request = ++logRequest.current;
    // This effect synchronizes the view with a new workspace request.
    // oxlint's synchronous-effect rule is intentionally suppressed for these
    // reset states because they must happen before the network response.
    queueMicrotask(() => {
      updateLoading(true);
      setData(null);
      resetSelection();
    });
    void fetchGitLog(workspaceId, 0)
      .then((next) => {
        if (
          epoch !== workspaceRequest.current ||
          request !== logRequest.current
        )
          return;
        setData(next);
      })
      .catch(() => {
        if (
          epoch === workspaceRequest.current &&
          request === logRequest.current
        )
          setData(null);
      })
      .finally(() => {
        if (
          epoch === workspaceRequest.current &&
          request === logRequest.current
        )
          updateLoading(false);
      });
  }, [workspaceId, resetSelection, updateLoading]);

  const selectCommit = useCallback(
    async (commit: GitLogCommit) => {
      const request = ++detailsRequest.current;
      const epoch = workspaceRequest.current;
      const requestedWorkspace = workspaceId;
      setSelectedCommit(commit);
      setSelectedDiff(null);
      setDiffLoading(true);
      onOpenCommitDetails?.({ commit, diff: null, loading: true });
      try {
        const diff = await fetchCommitDiff(requestedWorkspace, commit.sha);
        if (
          request !== detailsRequest.current ||
          epoch !== workspaceRequest.current ||
          requestedWorkspace !== workspaceId
        )
          return;
        setDiffLoading(false);
        setSelectedDiff(diff);
        onOpenCommitDetails?.({ commit, diff, loading: false });
      } catch {
        if (
          request !== detailsRequest.current ||
          epoch !== workspaceRequest.current ||
          requestedWorkspace !== workspaceId
        )
          return;
        setDiffLoading(false);
        setSelectedDiff(null);
        onOpenCommitDetails?.({
          commit,
          diff: null,
          loading: false,
          error: m.workbench_git_operation_failed(),
        });
      }
    },
    [workspaceId, onOpenCommitDetails],
  );

  const handleLoadMore = useCallback(async () => {
    if (!data || loadingMore) return;
    setLoadingMore(true);
    const next = await fetchGitLog(workspaceId, data.commits.length);
    setLoadingMore(false);
    if (!next) return;
    setData((prev) =>
      prev
        ? {
            branch: prev.branch,
            commits: [...prev.commits, ...next.commits],
            has_more: next.has_more,
          }
        : next,
    );
  }, [workspaceId, data, loadingMore]);

  const handleContextMenu = useCallback(
    (e: React.MouseEvent, commit: GitLogCommit) => {
      const commits = data?.commits ?? [];
      const idx = commits.findIndex((c) => c.sha === commit.sha);
      const older = commits[idx + 1];
      const newer = idx > 0 ? commits[idx - 1] : undefined;
      const items: ContextMenuItem[] = [
        {
          label: m.workbench_git_ctx_copy_sha(),
          onSelect: () => void navigator.clipboard.writeText(commit.sha),
        },
        {
          label: m.workbench_git_ctx_checkout(),
          onSelect: () =>
            void apiCheckout(workspaceId, commit.sha).then(onChanged),
        },
        {
          label: m.workbench_git_ctx_cherry_pick(),
          onSelect: () =>
            void apiCherryPick(workspaceId, commit.sha).then(onChanged),
        },
      ];
      if (older)
        items.push({
          label: m.workbench_git_ctx_move_down(),
          onSelect: () =>
            void apiReorder(workspaceId, [commit.sha, older.sha]).then(
              onChanged,
            ),
        });
      if (newer)
        items.push({
          label: m.workbench_git_ctx_move_up(),
          onSelect: () =>
            void apiReorder(workspaceId, [newer.sha, commit.sha]).then(
              onChanged,
            ),
        });
      if (squashBase === null)
        items.push({
          label: m.workbench_git_ctx_squash_select(),
          onSelect: () => setSquashBase(commit.sha),
        });
      else if (squashBase !== commit.sha) {
        const baseIdx = commits.findIndex((c) => c.sha === squashBase);
        if (baseIdx > idx)
          items.push({
            label: m.workbench_git_ctx_squash_here({ n: baseIdx - idx + 1 }),
            onSelect: () => {
              const message = window.prompt(
                m.workbench_diff_commit_placeholder(),
              );
              if (!message?.trim()) return;
              void apiSquash(workspaceId, squashBase, message.trim()).then(
                () => {
                  setSquashBase(null);
                  onChanged();
                },
              );
            },
          });
      }
      items.push({
        label: m.workbench_git_ctx_revert(),
        danger: true,
        onSelect: () => void apiRevert(workspaceId, commit.sha).then(onChanged),
      });
      menu.open(e, items);
    },
    [workspaceId, onChanged, menu, data, squashBase],
  );

  if (loading)
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      </div>
    );
  if (!data || data.commits.length === 0)
    return (
      <p className="px-4 py-8 text-center text-xs text-muted-foreground">
        {m.workbench_git_history_empty()}
      </p>
    );

  const filtered = data.commits.filter((commit) =>
    `${commit.sha} ${commit.message} ${commit.author}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      {menu.element}
      <div className="flex min-h-0 flex-1">
        <section
          className={`flex min-w-64 shrink-0 flex-col border-border/60 ${onOpenCommitDetails ? "w-full" : "w-[min(34%,21rem)] border-r"}`}
        >
          <label className="relative w-full shrink-0 overflow-hidden p-2">
            <Search className="pointer-events-none absolute left-4 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={m.workbench_git_history_search()}
              className="h-8 min-w-0 w-full rounded-md border border-border/60 bg-background px-7 text-xs outline-none focus:border-primary"
            />
          </label>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {filtered.map((commit) => (
              <button
                key={commit.sha}
                type="button"
                onClick={() => void selectCommit(commit)}
                onContextMenu={(event) => handleContextMenu(event, commit)}
                className={`flex w-full items-start border-b border-border/40 px-2 py-1.5 text-left transition-colors hover:bg-muted/30 ${selectedCommit?.sha === commit.sha ? "bg-muted/50" : ""}`}
              >
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 text-[8px] font-normal leading-[13px] text-muted-foreground">
                    <span className="font-mono font-normal text-foreground">
                      {commit.sha_short}
                    </span>
                    <span>{formatDate(commit.date)}</span>
                  </span>
                  <span className="mt-0.5 block text-[11px] font-normal leading-4 text-foreground/90">
                    {commit.message}
                  </span>
                  <span className="mt-0.5 block truncate text-[8px] font-normal leading-[13px] text-muted-foreground">
                    {commit.author.split("<")[0].trim()}
                  </span>
                </span>
              </button>
            ))}
            {data.has_more && !search && (
              <button
                type="button"
                onClick={() => void handleLoadMore()}
                disabled={loadingMore}
                className="w-full py-2 text-xs text-muted-foreground hover:bg-muted/30 disabled:opacity-50"
              >
                {loadingMore ? (
                  <Loader2 className="mx-auto h-3 w-3 animate-spin" />
                ) : (
                  m.workbench_git_history_load_more()
                )}
              </button>
            )}
          </div>
        </section>
        {!onOpenCommitDetails && (
          <section className="min-w-0 flex-1">
            {selectedCommit ? (
              <CommitDetails
                commit={selectedCommit}
                diff={selectedDiff}
                loading={diffLoading}
              />
            ) : (
              <EmptyCommitDetails />
            )}
          </section>
        )}
      </div>
    </div>
  );
}
