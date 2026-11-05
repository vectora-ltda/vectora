/** Clients de API do Files workbench — todas as chamadas ao backend
 * (`/workspaces/{id}/...`). O componente só consome; a lógica é do Python. */

import type { DiffSummary, FileEntry } from "@/lib/stores/workbench-store";

export interface SearchHit {
  path: string;
  line_number: number;
  line_text: string;
}

export interface SearchResult {
  hits: SearchHit[];
  truncated: boolean;
}

export interface FileLogEntry {
  sha: string;
  sha_short: string;
  author: string;
  date: string; // ISO 8601
  message: string;
}

export interface FileLogResponse {
  path: string;
  entries: FileLogEntry[];
}

export interface ShowFileAtRevResponse {
  path: string;
  sha: string;
  content: string | null;
  binary: boolean;
  truncated: boolean;
}

export async function fetchTree(
  workspaceId: string,
  path: string,
): Promise<FileEntry[] | null> {
  const qs = new URLSearchParams({ path });
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/tree?${qs}`,
  );
  if (!res.ok) return null;
  const data = await res.json();
  return data.entries ?? [];
}

export async function fetchDiffSummary(
  workspaceId: string,
): Promise<DiffSummary | null> {
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/git/diff`,
  );
  if (!res.ok) return null;
  return res.json();
}

export async function apiFsCreate(
  workspaceId: string,
  type: "file" | "dir",
  path: string,
): Promise<boolean> {
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/fs/${type}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    },
  );
  return res.ok;
}

export async function apiFsCreateFile(
  workspaceId: string,
  path: string,
  content: string,
): Promise<{ ok: boolean; message?: string }> {
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/fs/file`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, content }),
    },
  );
  const data = await res.json().catch(() => ({}));
  return {
    ok: res.ok,
    message: typeof data.message === "string" ? data.message : undefined,
  };
}

export async function apiFsDelete(
  workspaceId: string,
  path: string,
  permanent = false,
): Promise<boolean> {
  const qs = new URLSearchParams({ path });
  if (permanent) qs.set("permanent", "true");
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/fs?${qs}`,
    { method: "DELETE" },
  );
  return res.ok;
}

export async function apiFsMove(
  workspaceId: string,
  fromPath: string,
  toPath: string,
): Promise<{ ok: boolean; message?: string }> {
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/fs/move`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_path: fromPath, to_path: toPath }),
    },
  );
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, message: data.message };
}

export async function apiFsSearch(
  workspaceId: string,
  query: string,
  path = "",
): Promise<SearchResult | null> {
  const qs = new URLSearchParams({ q: query });
  if (path) qs.set("path", path);
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/fs/search?${qs}`,
  );
  if (!res.ok) return null;
  return res.json() as Promise<SearchResult>;
}

export async function apiFsGitLogFile(
  workspaceId: string,
  path: string,
  n = 50,
): Promise<FileLogResponse | null> {
  const qs = new URLSearchParams({ path, n: String(n) });
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/git/log/file?${qs}`,
  );
  if (!res.ok) return null;
  return res.json() as Promise<FileLogResponse>;
}

export async function apiFsGitShow(
  workspaceId: string,
  sha: string,
  path: string,
): Promise<ShowFileAtRevResponse | null> {
  const qs = new URLSearchParams({ sha, path });
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/git/show?${qs}`,
  );
  if (!res.ok) return null;
  return res.json() as Promise<ShowFileAtRevResponse>;
}
