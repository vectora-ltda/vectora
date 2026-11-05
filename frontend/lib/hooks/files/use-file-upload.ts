/**
 * File Upload Hook
 *
 * Custom hook for managing file uploads (images, code files, logs, etc.)
 * - Handles drag & drop, paste, and file selection
 * - Validates file types and sizes
 * - Converts files to base64 for API transmission
 * - Manages upload errors and loading states
 */

import { useState, useCallback } from "react";
import type { ImageAttachment } from "../../types";
import { createImageAttachment, validateImageFile } from "../../utils/chat";
import { IMAGE_UNSUPPORTED_MODEL_MESSAGE } from "../../constants/features";

// ============================================================================
// Types
// ============================================================================

export interface UseFileUploadReturn {
  attachedFiles: ImageAttachment[];
  uploadError: string | null;
  isDragging: boolean;
  handleFileSelect: (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => Promise<void>;
  handlePaste: (event: React.ClipboardEvent) => Promise<void>;
  handleDrop: (event: React.DragEvent) => Promise<void>;
  handleDragOver: (event: React.DragEvent) => void;
  handleDragLeave: (event: React.DragEvent) => void;
  removeFile: (fileId: string) => void;
  clearFiles: () => void;
  setUploadError: (error: string | null) => void;
  /** Processa um conjunto arbitrário de Files (drag/drop, paste, virtual). */
  processFiles: (files: File[]) => Promise<void>;
}

interface UseFileUploadOptions {
  disableImageUploads?: boolean;
}

const isImageFile = (file: File): boolean =>
  file.type.startsWith("image/") || /\.(jpe?g|png|gif|webp)$/i.test(file.name);

// ============================================================================
// Hook Implementation
// ============================================================================

/**
 * Hook to manage file uploads with drag & drop, paste, and file selection support.
 *
 * @returns File upload state and handlers
 *
 * @example
 * ```tsx
 * const { attachedFiles, handleFileSelect, handlePaste, handleDrop, removeFile } = useFileUpload()
 *
 * return (
 *   <div onDrop={handleDrop} onPaste={handlePaste}>
 *     <input type="file" onChange={handleFileSelect} />
 *     {attachedFiles.map(file => (
 *       <FilePreview key={file.id} file={file} onRemove={removeFile} />
 *     ))}
 *   </div>
 * )
 * ```
 */
export function useFileUpload(
  options: UseFileUploadOptions = {},
): UseFileUploadReturn {
  const [attachedFiles, setAttachedFiles] = useState<ImageAttachment[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const disableImageUploads = options.disableImageUploads ?? false;

  /**
   * Process multiple files and add them to attached files list.
   * Validates each file and converts to base64.
   */
  const processFiles = useCallback(
    async (files: File[]) => {
      setUploadError(null);
      // Cada arquivo é independente; processar em paralelo preserva a ordem
      // do array de entrada porque Promise.all mantém a ordem dos resultados.
      const results = await Promise.all(
        files.map(async (file) => {
          const validation = validateImageFile(file);
          if (!validation.valid) {
            return { error: validation.error || "Invalid file" } as const;
          }
          const isImage = isImageFile(file);
          if (isImage && disableImageUploads) {
            return { error: IMAGE_UNSUPPORTED_MODEL_MESSAGE } as const;
          }
          try {
            const textLength = isImage ? undefined : (await file.text()).length;
            const imageAttachment = await createImageAttachment(file);
            imageAttachment.textLength = textLength;
            return { attachment: imageAttachment } as const;
          } catch (error) {
            console.error("Error processing file:", error);
            return { error: "Failed to process file" } as const;
          }
        }),
      );

      const attachments = results.flatMap((r): ImageAttachment[] =>
        "attachment" in r ? [r.attachment as ImageAttachment] : [],
      );
      const lastError = results.toReversed().find((r) => "error" in r);
      if (lastError && "error" in lastError)
        setUploadError((lastError as { error: string }).error);
      if (attachments.length > 0) {
        setAttachedFiles((prev) => [...prev, ...attachments]);
      }
    },
    [disableImageUploads],
  );

  /**
   * Handle file selection from input element.
   */
  const handleFileSelect = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = event.target.files;
      if (!files || files.length === 0) return;

      await processFiles(Array.from(files));

      // Reset file input
      event.target.value = "";
    },
    [processFiles],
  );

  /**
   * Handle paste events (images from clipboard).
   */
  const handlePaste = useCallback(
    async (event: React.ClipboardEvent) => {
      const items = event.clipboardData?.items;
      if (!items) return;

      setUploadError(null);

      // Coleta os arquivos de imagem do clipboard primeiro (síncrono),
      // depois processa todos em paralelo para evitar await dentro de loop.
      const imageFiles: File[] = [];
      for (const item of Array.from(items)) {
        if (!item.type.startsWith("image/")) continue;
        event.preventDefault();
        if (disableImageUploads) {
          setUploadError(IMAGE_UNSUPPORTED_MODEL_MESSAGE);
          continue;
        }
        const file = item.getAsFile();
        if (file) imageFiles.push(file);
      }

      if (imageFiles.length === 0) return;
      await processFiles(imageFiles);
    },
    [disableImageUploads, processFiles],
  );

  /**
   * Handle drag over event.
   */
  const handleDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    setIsDragging(true);
  }, []);

  /**
   * Handle drag leave event.
   */
  const handleDragLeave = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    setIsDragging(false);
  }, []);

  /**
   * Handle drop event.
   */
  const handleDrop = useCallback(
    async (event: React.DragEvent) => {
      event.preventDefault();
      setIsDragging(false);
      setUploadError(null);

      const files = event.dataTransfer?.files;
      if (!files || files.length === 0) return;

      await processFiles(Array.from(files));
    },
    [processFiles],
  );

  /**
   * Remove a file from the attached files list.
   */
  const removeFile = useCallback((fileId: string) => {
    setAttachedFiles((prev) => {
      const file = prev.find((f) => f.id === fileId);
      // Revoke object URL to free memory
      if (file?.url) {
        URL.revokeObjectURL(file.url);
      }
      return prev.filter((f) => f.id !== fileId);
    });
  }, []);

  /**
   * Clear all attached files.
   * Note: We don't revoke URLs here because the message that was just sent
   * still needs them for rendering. URLs will be cleaned up by the browser
   * when the page is closed or refreshed.
   */
  const clearFiles = useCallback(() => {
    setAttachedFiles([]);
    setUploadError(null);
  }, []);

  return {
    attachedFiles,
    uploadError,
    isDragging,
    handleFileSelect,
    handlePaste,
    handleDrop,
    handleDragOver,
    handleDragLeave,
    removeFile,
    clearFiles,
    setUploadError,
    processFiles,
  };
}
