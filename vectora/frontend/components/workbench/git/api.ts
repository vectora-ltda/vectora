/**
 * Cliente HTTP do painel Git — centraliza as chamadas REST do workspace
 * (`/workspaces/{id}/git/*` e `/workspaces/{id}/pr`). Cada função degrada
 * para `null`/lista vazia em falha; quem chama decide o feedback.
 */

import type { DiffHunk, DiffSummary } from "@/lib/stores/workbench-store";

function base(workspaceId: string): string {
  return `/workspaces/${encodeURIComponent(workspaceId)}`;
}

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

export async function fetchGitOperation(
  workspaceId: string,
): Promise<GitOperation | null> {
  const res = await fetch(`${base(workspaceId)}/git/operation`);
  if (!res.ok) return null;
  const data = (await res.json()) as { operation?: GitOperation | null };
  return data.operation ?? null;
}

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

export async function fetchBranches(
  workspaceId: string,
): Promise<GitBranches | null> {
  const res = await fetch(`${base(workspaceId)}/git/branches`);
  if (!res.ok) return null;
  return res.json() as Promise<GitBranches>;
}

export function apiCheckout(
  workspaceId: string,
  ref: string,
  create = false,
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/checkout`, { ref, create });
}

// ── Sync (fetch / pull / push) ───────────────────────────────────────────────

export function apiSync(
  workspaceId: string,
  action: "fetch" | "pull" | "push",
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/${action}`, {});
}

// ── Merge ─────────────────────────────────────────────────────────────────

export interface MergeResult {
  status: "ok" | "conflict" | "error";
  message: string;
  conflicts: string[];
}

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

export async function fetchDiff(
  workspaceId: string,
): Promise<DiffSummary | null> {
  const res = await fetch(`${base(workspaceId)}/git/diff`);
  if (!res.ok) return null;
  return res.json();
}

export async function fetchDiffFile(
  workspaceId: string,
  path: string,
): Promise<DiffHunk[] | null> {
  const qs = new URLSearchParams({ path });
  const res = await fetch(`${base(workspaceId)}/git/diff/file?${qs}`);
  if (!res.ok) return null;
  const data = await res.json();
  return data.hunks ?? [];
}

export function apiGitFileAction(
  workspaceId: string,
  action: "stage" | "unstage" | "discard",
  path: string,
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/${action}`, { path });
}

export async function apiGitCommit(
  workspaceId: string,
  message: string,
  dryRunHooks = false,
  opts: { body?: string; amend?: boolean } = {},
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/commit`, {
    message,
    dry_run_hooks: dryRunHooks,
    body: opts.body || null,
    amend: opts.amend ?? false,
  });
}

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

export function apiReorder(
  workspaceId: string,
  commits: string[],
): Promise<{ status: string; message: string }> {
  return postJson(`${base(workspaceId)}/git/reorder`, { commits });
}

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
  refs: string[];
}

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

export async function fetchCommitDiff(
  workspaceId: string,
  sha: string,
): Promise<string> {
  const qs = new URLSearchParams({ sha });
  const res = await fetch(`${base(workspaceId)}/git/commit/diff?${qs}`);
  if (!res.ok) return "";
  const data = await res.json();
  return (data.diff as string) ?? "";
}

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

export async function apiListConflicts(workspaceId: string): Promise<string[]> {
  const res = await fetch(`${base(workspaceId)}/git/conflicts`);
  if (!res.ok) return [];
  const data = await res.json();
  return ((data.files as { path: string }[]) ?? []).map((f) => f.path);
}

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

export async function fetchWorktrees(
  workspaceId: string,
): Promise<WorktreeEntry[]> {
  const res = await fetch(`${base(workspaceId)}/worktrees`);
  if (!res.ok) return [];
  const data = await res.json();
  return (data.worktrees as WorktreeEntry[]) ?? [];
}

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

export async function fetchPullRequests(
  workspaceId: string,
): Promise<{ available: boolean; prs: PullRequest[] }> {
  const res = await fetch(`${base(workspaceId)}/pr`);
  if (!res.ok) return { available: false, prs: [] };
  const data = await res.json();
  return { available: data.available ?? false, prs: data.prs ?? [] };
}

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
