import type { ImageAttachment } from "../../types";
import { IMAGE_UNSUPPORTED_MODEL_MESSAGE } from "../../constants/features";
import { isTextKind, mediaKindOf } from "./media-kind";
import { createImageAttachment, validateImageFile } from "./validation";

type FileResult = { attachment: ImageAttachment } | { error: string };

export interface ProcessedAttachments {
  attachments: ImageAttachment[];
  /** Último erro entre os arquivos recusados; `null` se todos entraram. */
  error: string | null;
}

/**
 * Valida cada arquivo (tamanho por tipo, modelo sem suporte a imagem) e o converte
 * em anexo. Arrastar, colar, escolher e "Abrir com Vectora" passam por aqui, então
 * as quatro entradas aceitam e recusam os mesmos arquivos.
 */
export async function processAttachmentFiles(
  files: File[],
  options: { disableImageUploads?: boolean } = {},
): Promise<ProcessedAttachments> {
  // Cada arquivo é independente; processar em paralelo preserva a ordem do
  // array de entrada porque Promise.all mantém a ordem dos resultados.
  const results = await Promise.all(
    files.map(async (file): Promise<FileResult> => {
      const validation = validateImageFile(file);
      if (!validation.valid) {
        return { error: validation.error || "Invalid file" };
      }
      const isImage = mediaKindOf(file.type, file.name) === "image";
      if (isImage && options.disableImageUploads) {
        return { error: IMAGE_UNSUPPORTED_MODEL_MESSAGE };
      }
      try {
        // Só arquivos de texto têm contagem de caracteres; ler binário
        // (PDF, áudio, vídeo) como texto não tem significado.
        const textLength = isTextKind(mediaKindOf(file.type, file.name))
          ? (await file.text()).length
          : undefined;
        const attachment = await createImageAttachment(file);
        attachment.textLength = textLength;
        return { attachment };
      } catch (error) {
        console.error("Error processing file:", error);
        return { error: "Failed to process file" };
      }
    }),
  );

  const attachments = results.flatMap((result): ImageAttachment[] =>
    "attachment" in result ? [result.attachment] : [],
  );
  const lastError = results
    .toReversed()
    .find((result): result is { error: string } => "error" in result);
  return { attachments, error: lastError?.error ?? null };
}
