/**
 * Cliente HTTP do painel Git — centraliza as chamadas REST do workspace
 * (`/workspaces/{id}/git/*` e `/workspaces/{id}/pr`). Cada função degrada
 * para `null`/lista vazia em falha; quem chama decide o feedback.
 */

import type { DiffHunk, DiffSummary } from "@/lib/stores/workbench-store";

/** Build a workspace-scoped endpoint with an encoded identifier. */
function base(workspaceId: string): string {
  return `/workspaces/${encodeURIComponent(workspaceId)}`;
}

/** Post an operation payload; transport errors remain visible to the caller. */
async function postJson(
  url: string,
  body: unknown,
): Promise<{ status: string; message: string }> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await res.json().catch(() => ({ status: "error", message: "" }))) as {
    status: string;
    message: string;
  };
}

// ── Status / branches ───────────────────────────────────────────────────────

export interface GitStatus {
  is_git_repo: boolean;
  branch: string;
  clean: boolean;
  ahead: number;
  behind: number;
  operation_in_progress?: GitOperation | null;
}

export interface GitOperation {
  operation_id: string;
  workspace_id: string;
  operation: string;
  state: "queued" | "running" | "succeeded" | "failed";
  phase: string;
  progress: number;
  output: string;
  error_code: string | null;
  error: string | null;
  created_at: number;
  finished_at: number | null;
}

/** Read the current operation snapshot, or null for an unsuccessful response. */
export async function fetchGitOperation(
  workspaceId: string,
): Promise<GitOperation | null> {
  const res = await fetch(`${base(workspaceId)}/git/operation`);
  if (!res.ok) return null;
  const data = (await res.json()) as { operation?: GitOperation | null };
  return data.operation ?? null;
}

/** Read bounded operation history for the active repository. */
export async function fetchGitOperationHistory(
  workspaceId: string,
  limit = 50,
): Promise<GitOperation[]> {
  const res = await fetch(`${base(workspaceId)}/git/operations?limit=${limit}`);
  if (!res.ok) return [];
  const data = (await res.json()) as { operations?: GitOperation[] };
  return data.operations ?? [];
}

export interface GitCommitSuggestion {
  title: string;
  description: string;
}

/** Read the proposed commit title and description without committing. */
export async function fetchGitCommitSuggestion(
  workspaceId: string,
): Promise<GitCommitSuggestion | null> {
  const res = await fetch(`${base(workspaceId)}/git/commit/suggestion`);
  if (!res.ok) return null;
  return (await res.json()) as GitCommitSuggestion;
}

/** Read branch, cleanliness and ahead/behind counters for the toolbar. */
export async function fetchGitStatus(
  workspaceId: string,
): Promise<GitStatus | null> {
  const res = await fetch(`${base(workspaceId)}/git/status`);
  if (!res.ok) return null;
  return res.json() as Promise<GitStatus>;
}

export interface GitBranches {
  current: string;
  branches: string[];
  remotes: string[];
}

/** Read local and remote branch names for the active workspace. */
export async function fetchBranches(
  workspaceId: string,
): Promise<GitBranches | null> {
  const res = await fetch(`${base(workspaceId)}/git/branches`);
  if (!res.ok) return null;
  return res.json() as Promise<GitBranches>;
}

/** Checkout a reference, optionally creating the requested branch. */
export function apiCheckout(
  workspaceId: string,
  ref: string,
  create = false,
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/checkout`, { ref, create });
}

// ── Sync (fetch / pull / push) ───────────────────────────────────────────────

/** Request fetch, pull or push; callers handle rejected network requests. */
export function apiSync(
  workspaceId: string,
  action: "fetch" | "pull" | "push",
  options: { force?: boolean } = {},
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/${action}`, options);
}

// ── Merge ─────────────────────────────────────────────────────────────────

export interface MergeResult {
  status: "ok" | "conflict" | "error";
  message: string;
  conflicts: string[];
}

/** Merge the selected branch and retain the backend conflict details. */
export async function apiMerge(
  workspaceId: string,
  branch: string,
): Promise<MergeResult> {
  const res = await fetch(`${base(workspaceId)}/git/merge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ branch }),
  });
  return (await res.json().catch(() => ({
    status: "error",
    message: "",
    conflicts: [],
  }))) as MergeResult;
}

// ── Compare (estilo VS Code) ────────────────────────────────────────────────

export interface CompareFile {
  path: string;
  status: string;
  additions: number;
  deletions: number;
}

export interface CompareResult {
  base: string;
  head: string;
  ahead: number;
  behind: number;
  files: CompareFile[];
  truncated: boolean;
}

/** Compare two references without changing the working tree. */
export async function apiCompare(
  workspaceId: string,
  baseRef: string,
  head: string,
): Promise<CompareResult | null> {
  const qs = new URLSearchParams({ base: baseRef, head });
  const res = await fetch(`${base(workspaceId)}/git/compare?${qs}`);
  if (!res.ok) return null;
  return res.json() as Promise<CompareResult>;
}

/** Read diff hunks for one file between two selected references. */
export async function apiCompareFile(
  workspaceId: string,
  baseRef: string,
  head: string,
  path: string,
): Promise<DiffHunk[]> {
  const qs = new URLSearchParams({ base: baseRef, head, path });
  const res = await fetch(`${base(workspaceId)}/git/compare/file?${qs}`);
  if (!res.ok) return [];
  const data = await res.json();
  return (data.hunks as DiffHunk[]) ?? [];
}

// ── Diff do working tree (aba Mudanças) ─────────────────────────────────────

/** Read the working-tree summary used by the Changes view. */
export async function fetchGitDiff(
  workspaceId: string,
): Promise<DiffSummary | null> {
  const res = await fetch(`${base(workspaceId)}/git/diff`);
  if (!res.ok) return null;
  return res.json();
}

/** Read working-tree diff hunks for one encoded file path. */
export async function fetchGitDiffFile(
  workspaceId: string,
  path: string,
): Promise<DiffHunk[] | null> {
  const qs = new URLSearchParams({ path });
  const res = await fetch(`${base(workspaceId)}/git/diff/file?${qs}`);
  if (!res.ok) return null;
  const data = await res.json();
  return data.hunks ?? [];
}

/** Stage, unstage or discard the explicitly selected file. */
export function apiGitFileAction(
  workspaceId: string,
  action: "stage" | "unstage" | "discard",
  path: string,
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/${action}`, { path });
}

/** Append the selected file or directory to workspace ignore rules. */
export function apiGitignoreAppend(
  workspaceId: string,
  path: string,
  isFolder = false,
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/fs/gitignore/append`, {
    path,
    is_folder: isFolder,
  });
}

/** Submit the commit message and explicit hook, amend and signoff preferences. */
export async function apiGitCommit(
  workspaceId: string,
  message: string,
  dryRunHooks = false,
  opts: {
    body?: string;
    amend?: boolean;
    runHooks?: boolean;
    signoff?: boolean;
    bypass?: boolean;
  } = {},
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/commit`, {
    message,
    dry_run_hooks: dryRunHooks,
    body: opts.body || null,
    amend: opts.amend ?? false,
    ...(opts.runHooks ? { run_hooks: true } : {}),
    ...(opts.signoff ? { signoff: true } : {}),
    ...(opts.bypass ? { bypass: true } : {}),
  });
}

/** Squash from the selected base using the supplied commit message. */
export function apiSquash(
  workspaceId: string,
  baseRef: string,
  message: string,
  body?: string,
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/squash`, {
    base_ref: baseRef,
    message,
    body: body || null,
  });
}

/** Request the exact commit ordering selected by the user. */
export function apiReorder(
  workspaceId: string,
  commits: string[],
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/reorder`, { commits });
}

/** Apply a commit, optionally leaving its changes uncommitted. */
export function apiCherryPick(
  workspaceId: string,
  sha: string,
  noCommit = false,
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/cherry-pick`, {
    sha,
    no_commit: noCommit,
  });
}

// ── Histórico ───────────────────────────────────────────────────────────────

export interface GitLogCommit {
  sha: string;
  sha_short: string;
  author: string;
  date: string;
  message: string;
  body?: string;
  refs: string[];
}

/** Read a page of 50 commits with the backend continuation flag. */
export async function fetchGitLog(
  workspaceId: string,
  offset = 0,
): Promise<{
  branch: string;
  commits: GitLogCommit[];
  has_more: boolean;
} | null> {
  const qs = new URLSearchParams({ n: "50", offset: String(offset) });
  const res = await fetch(`${base(workspaceId)}/git/log?${qs}`);
  if (!res.ok) return null;
  return res.json();
}

/** Read the textual diff of a selected commit. */
export async function fetchGitCommitDiff(
  workspaceId: string,
  sha: string,
): Promise<string> {
  const qs = new URLSearchParams({ sha });
  const res = await fetch(`${base(workspaceId)}/git/commit/diff?${qs}`);
  if (!res.ok) return "";
  const data = await res.json();
  return (data.diff as string) ?? "";
}

/** Apply a reverse patch without automatically creating a commit. */
export function apiRevert(
  workspaceId: string,
  sha: string,
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/revert`, { sha, no_commit: true });
}

// ── Stash ─────────────────────────────────────────────────────────────────

export interface StashEntry {
  index: number;
  label: string;
}

/** Perform the selected stash operation and return the resulting entries. */
export async function apiStash(
  workspaceId: string,
  action: "list" | "push" | "pop" | "apply" | "drop",
  opts: { name?: string; index?: number } = {},
): Promise<{ entries: StashEntry[]; message: string }> {
  const res = await fetch(`${base(workspaceId)}/git/stash`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...opts }),
  });
  if (!res.ok) return { entries: [], message: "" };
  const data = await res.json();
  return { entries: data.entries ?? [], message: data.message ?? "" };
}

// ── Conflitos ───────────────────────────────────────────────────────────────

/** Read unresolved paths, returning an empty list on an unsuccessful response. */
export async function apiListConflicts(workspaceId: string): Promise<string[]> {
  const res = await fetch(`${base(workspaceId)}/git/conflicts`);
  if (!res.ok) return [];
  const data = await res.json();
  return ((data.files as { path: string }[]) ?? []).map((f) => f.path);
}

/** Resolve one path using the explicitly selected side. */
export function apiResolveConflict(
  workspaceId: string,
  path: string,
  resolution: "ours" | "theirs",
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/resolve-conflict`, {
    path,
    resolution,
  });
}

// ── Worktrees ───────────────────────────────────────────────────────────────

export interface WorktreeEntry {
  path: string;
  branch?: string;
  head?: string;
}

/** List linked worktrees of the active repository. */
export async function fetchWorktrees(
  workspaceId: string,
): Promise<WorktreeEntry[]> {
  const res = await fetch(`${base(workspaceId)}/worktrees`);
  if (!res.ok) return [];
  const data = await res.json();
  return (data.worktrees as WorktreeEntry[]) ?? [];
}

/** Create the requested worktree and report HTTP success. */
export async function apiCreateWorktree(
  workspaceId: string,
  name: string,
  branch?: string,
): Promise<boolean> {
  const res = await fetch(`${base(workspaceId)}/worktrees`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace_id: workspaceId, name, branch }),
  });
  return res.ok;
}

// ── Pull requests (gh) ────────────────────────────────────────────────────

export interface PullRequest {
  number: number;
  title: string;
  state: string;
  author: string;
  head: string;
  base: string;
}

/** Read pull requests and whether the GitHub integration is available. */
export async function fetchPullRequests(
  workspaceId: string,
): Promise<{ available: boolean; prs: PullRequest[] }> {
  const res = await fetch(`${base(workspaceId)}/pr`);
  if (!res.ok) return { available: false, prs: [] };
  const data = await res.json();
  return { available: data.available ?? false, prs: data.prs ?? [] };
}

/** Create a pull request from the supplied title, body and base branch. */
export function apiCreatePR(
  workspaceId: string,
  title: string,
  body: string,
  baseBranch: string,
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/pr`, {
    title,
    body,
    base: baseBranch,
  });
}
