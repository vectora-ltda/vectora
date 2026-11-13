/**
 * Utilitários de attachments para o pipeline multimodal (F1).
 *
 * Converte o tipo interno `ImageAttachment` (frontend) para o tipo `Attachment`
 * que o backend espera em `StreamChatRequest.attachments`.
 */

import type { ImageAttachment } from "@/lib/types";
import type { Attachment, AttachmentKind } from "@/lib/api/vectora-client";
import { mediaKindOf } from "./media-kind";

/**
 * Deriva o `AttachmentKind` declarado ao backend. É só uma dica: o backend
 * reclassifica pelos bytes. Arquivos de texto/código seguem como "code".
 */
function deriveKind(mimeType: string, name: string): AttachmentKind {
  const kind = mediaKindOf(mimeType, name);
  return kind === "text" ? "code" : kind;
}

/**
 * Converte a lista de `ImageAttachment` do estado local para o formato
 * `Attachment[]` esperado pela API do backend.
 *
 * Arquivos sem `base64` (ex.: URLs externas ainda não carregadas) são ignorados.
 */
export function toApiAttachments(files: ImageAttachment[]): Attachment[] {
  return files
    .filter((f) => Boolean(f.base64) && Boolean(f.name))
    .map((f) => ({
      kind: deriveKind(f.mimeType, f.name ?? ""),
      name: f.name ?? "unknown",
      mime_type: f.mimeType,
      // O campo base64 já armazena dados puros (sem prefixo data URL)
      // graças ao fileToBase64() em validation.ts.
      base64_data: f.base64!,
    }));
}
