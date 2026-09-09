import type { Env } from "../gateway/types";

const API = "https://api.github.com";
const DEFAULT_INTAKE_REPO = "vectora-ltda/vectora-issues";
const DEFAULT_CORE_REPO = "vectora-ltda/vectora";

export interface GitHubIssue {
  number: number;
  title: string;
  body: string | null;
  state: "open" | "closed";
  html_url: string;
  repository_url?: string;
  user?: { login?: string };
}

export interface GitHubComment {
  id: number;
  body: string;
  html_url: string;
  created_at: string;
  user?: { login?: string };
}

interface CreateIssueResponse extends GitHubIssue {
  labels?: Array<{ name?: string }>;
}

export class GitHubIssueError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "GitHubIssueError";
    this.status = status;
  }
}

export function intakeRepo(env: Env): string {
  return env.GITHUB_ISSUES_REPO?.trim() || DEFAULT_INTAKE_REPO;
}

export function coreRepo(env: Env): string {
  return env.GITHUB_CORE_REPO?.trim() || DEFAULT_CORE_REPO;
}

function token(env: Env): string {
  return env.GITHUB_ISSUES_TOKEN?.trim() || env.GITHUB_TOKEN?.trim() || "";
}

function repoPath(repo: string): string {
  const [owner, name, extra] = repo.split("/");
  if (
    !owner ||
    !name ||
    extra ||
    !/^[A-Za-z0-9_.-]+$/.test(owner) ||
    !/^[A-Za-z0-9_.-]+$/.test(name)
  ) {
    throw new GitHubIssueError(500, "github_repo_invalid");
  }
  return `repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`;
}

async function request<T>(
  env: Env,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const accessToken = token(env);
  if (!accessToken) throw new GitHubIssueError(503, "github_not_configured");

  const headers = new Headers(init.headers);
  headers.set("Accept", "application/vnd.github+json");
  headers.set("X-GitHub-Api-Version", "2022-11-28");
  headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body !== undefined) headers.set("Content-Type", "application/json");

  const response = await fetch(`${API}/${path}`, { ...init, headers });
  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? (JSON.parse(text) as unknown) : null;
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const message =
      typeof payload === "object" && payload !== null && "message" in payload
        ? String((payload as { message: unknown }).message)
        : `github_http_${response.status}`;
    throw new GitHubIssueError(response.status, message.slice(0, 200));
  }
  return payload as T;
}

export async function createIssue(
  env: Env,
  repo: string,
  title: string,
  body: string,
  labels: string[] = [],
): Promise<CreateIssueResponse> {
  return request<CreateIssueResponse>(env, `${repoPath(repo)}/issues`, {
    method: "POST",
    body: JSON.stringify({ title, body, labels }),
  });
}

export async function updateIssue(
  env: Env,
  repo: string,
  number: number,
  patch: { title?: string; body?: string; state?: "open" | "closed" },
): Promise<GitHubIssue> {
  return request<GitHubIssue>(env, `${repoPath(repo)}/issues/${number}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function addComment(
  env: Env,
  repo: string,
  number: number,
  body: string,
): Promise<GitHubComment> {
  return request<GitHubComment>(
    env,
    `${repoPath(repo)}/issues/${number}/comments`,
    {
      method: "POST",
      body: JSON.stringify({ body }),
    },
  );
}

export async function listComments(
  env: Env,
  repo: string,
  number: number,
): Promise<GitHubComment[]> {
  return request<GitHubComment[]>(
    env,
    `${repoPath(repo)}/issues/${number}/comments?per_page=100`,
  );
}
