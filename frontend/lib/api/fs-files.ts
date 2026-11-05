/**
 * Helpers de leitura/escrita de arquivos do workspace.
 *
 * Compartilhados entre o explorador (`files-tab`) e o editor em janela
 * (`file-editor`). O backend é a fonte de verdade: a escrita usa
 * `expected_sha256` para detectar conflito otimista (HTTP 412).
 */

import type { FileContent } from "@/lib/stores/workbench-store";

export type SaveFileResult =
  | { ok: true; sha256: string | null }
  | { ok: false; conflict: boolean; message?: string };

/** Lê o conteúdo de um arquivo de texto (truncado pelo backend se grande). */
export async function fetchFile(
  workspaceId: string,
  path: string,
): Promise<FileContent | null> {
  const qs = new URLSearchParams({ path });
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/file?${qs}`,
  );
  if (!res.ok) return null;
  return res.json();
}

/** Grava o conteúdo; HTTP 412 vira `conflict` para resolução no chamador. */
export async function apiUpdateFile(
  workspaceId: string,
  path: string,
  content: string,
  expectedSha256: string | null,
): Promise<SaveFileResult> {
  const qs = new URLSearchParams({ path });
  const res = await fetch(
    `/workspaces/${encodeURIComponent(workspaceId)}/fs/file?${qs}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, expected_sha256: expectedSha256 }),
    },
  );
  if (res.status === 412) {
    return { ok: false, conflict: true };
  }
  let data: { status?: string; message?: string; sha256?: string | null } = {};
  try {
    data = await res.json();
  } catch {
    // resposta sem corpo JSON — segue com `data` vazio
  }
  if (!res.ok || data.status !== "ok") {
    return { ok: false, conflict: false, message: data.message };
  }
  return { ok: true, sha256: data.sha256 ?? null };
}
