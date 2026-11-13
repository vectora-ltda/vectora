/**
 * File Validation Utilities
 *
 * Functions for validating files before upload.
 */

import { generateMessageId } from "./message-helpers";
import type { ImageAttachment } from "../../types";
import { maxBytesFor, mediaKindOf } from "./media-kind";

/**
 * Convert a File object to a base64 string.
 * Used for encoding images before sending to the backend.
 */
export const fileToBase64 = (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const result = reader.result as string;
      // Remove the data URL prefix (e.g., "data:image/png;base64,")
      const base64 = result.split(",")[1];
      resolve(base64);
    });
    reader.addEventListener("error", () => reject(reader.error));
    reader.readAsDataURL(file);
  });
};

/**
 * Create an ImageAttachment from a File object.
 * Converts the file to base64 and creates a preview URL.
 */
export const createImageAttachment = async (
  file: File,
): Promise<ImageAttachment> => {
  const base64 = await fileToBase64(file);
  const url = URL.createObjectURL(file);

  return {
    id: generateMessageId(),
    base64,
    url,
    mimeType: file.type,
    name: file.name,
    size: file.size,
  };
};

/**
 * Validate a file before upload.
 *
 * Every file type is accepted: the backend classifies it by its bytes and
 * decides how it reaches the model (native file, extracted text,
 * transcription or a reference). Only the per-kind size limit is checked
 * here, mirroring `backend/services/media_types.py`.
 */
export const validateImageFile = (
  file: File,
): { valid: boolean; error?: string } => {
  if (file.size === 0) {
    return { valid: false, error: "File is empty" };
  }
  const kind = mediaKindOf(file.type, file.name);
  const maxSize = maxBytesFor(kind, file.name);
  if (file.size > maxSize) {
    return {
      valid: false,
      error: `File must be smaller than ${Math.round(maxSize / (1024 * 1024))}MB`,
    };
  }
  return { valid: true };
};
