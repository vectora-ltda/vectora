/**
 * Utilitários de attachments para o pipeline multimodal (F1).
 *
 * Converte o tipo interno `ImageAttachment` (frontend) para o tipo `Attachment`
 * que o backend espera em `StreamChatRequest.attachments`.
 */

import type { ImageAttachment } from "@/lib/types";
import type { Attachment, AttachmentKind } from "@/lib/api/vectora-client";

/**
 * Deriva o `AttachmentKind` a partir do MIME type do arquivo.
 *
 * - `image/*` → "image"
 * - `application/pdf` → "pdf"
 * - `audio/*` → "audio"
 * - qualquer outra coisa → "code" (tratado como código/texto pelo backend)
 */
function deriveKind(mimeType: string): AttachmentKind {
  if (mimeType.startsWith("image/")) return "image";
  if (mimeType === "application/pdf") return "pdf";
  if (mimeType.startsWith("audio/")) return "audio";
  // txt, json, yaml, markdown, código-fonte, etc.
  return "code";
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
      kind: deriveKind(f.mimeType),
      name: f.name ?? "unknown",
      mime_type: f.mimeType,
      // O campo base64 já armazena dados puros (sem prefixo data URL)
      // graças ao fileToBase64() em validation.ts.
      base64_data: f.base64!,
    }));
}
