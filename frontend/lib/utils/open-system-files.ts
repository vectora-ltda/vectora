import type { ImageAttachment } from "@/lib/types";
import type { VectoraOpenedPath } from "@/lib/types/vectora-bridge";
import { processAttachmentFiles } from "@/lib/utils/chat/process-files";
import { m } from "@/lib/paraglide/messages";

export interface OpenSystemFilesDeps {
  /** Registra a pasta como workspace; devolve o id, ou `null` se falhou (o store já avisou). */
  createWorkspace: (path: string) => Promise<string | null>;
  addAttachments: (attachments: ImageAttachment[]) => void;
  /** Abre uma conversa nova (no workspace informado, se houver). */
  openNewSession: (workspaceId: string | null) => void;
  notifyError: (message: string) => void;
}

function errorMessage(
  item: Extract<VectoraOpenedPath, { kind: "error" }>,
): string {
  switch (item.reason) {
    case "missing":
      return m.open_files_error_missing({ name: item.name });
    case "too_large":
      return m.open_files_error_too_large({ name: item.name });
    case "unsupported":
      return m.open_files_error_unsupported({ name: item.name });
    default:
      return m.open_files_error_unreadable({ name: item.name });
  }
}

/**
 * O que fazer com arquivos e pastas que o sistema entregou ao app: pastas viram
 * workspaces e os arquivos viram anexos de uma conversa nova (no workspace aberto,
 * se houver), pelo mesmo caminho do arrastar e soltar. Erros viram aviso e não
 * impedem os demais itens.
 */
export async function openSystemFiles(
  items: readonly VectoraOpenedPath[],
  deps: OpenSystemFilesDeps,
): Promise<void> {
  for (const item of items) {
    if (item.kind === "error") deps.notifyError(errorMessage(item));
  }

  let workspaceId: string | null = null;
  for (const item of items) {
    if (item.kind !== "directory") continue;
    const id = await deps.createWorkspace(item.path);
    if (id) workspaceId = id;
  }

  const files = items
    .filter(
      (item): item is Extract<VectoraOpenedPath, { kind: "file" }> =>
        item.kind === "file",
    )
    .map(
      (item) =>
        new File([new Uint8Array(item.data)], item.name, {
          type: item.mimeType,
        }),
    );
  let attached = false;
  if (files.length > 0) {
    const { attachments, error } = await processAttachmentFiles(files);
    if (error)
      deps.notifyError(m.open_files_error_attachment({ detail: error }));
    if (attachments.length > 0) {
      deps.addAttachments(attachments);
      attached = true;
    }
  }

  if (attached || workspaceId) deps.openNewSession(workspaceId);
}

/** `vectora://session/<id>` — o único destino de deep-link que o app aceita. */
export function sessionIdFromDeepLink(url: string): string | null {
  const match = /^vectora:\/\/session\/([A-Za-z0-9_-]{1,64})\/?$/.exec(url);
  return match ? match[1]! : null;
}
