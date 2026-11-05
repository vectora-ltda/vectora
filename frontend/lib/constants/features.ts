/**
 * Feature Constants
 *
 * Application-wide feature flags and limits.
 */

export const THREAD_FETCH_LIMIT = 100;

export const DEFAULT_TITLE_MAX_LENGTH = 60;

/** Texto colado acima deste tamanho vira anexo de texto (`pasted.txt`)
 *  ao invés de inflar o textarea. Inspirado no padrão do ChatGPT/Claude:
 *  o conteúdo fica acessível ao agente como artefato, sem poluir a UI. */
export const LARGE_PASTE_THRESHOLD = 4_000;

export const IMAGE_UNSUPPORTED_MODEL_MESSAGE =
  "Selected model does not support image uploads";

export const STORAGE_KEYS = {
  CLIENT_PROFILE: "client-profile",
} as const;

export const FEEDBACK_KEY = "ux.thumb_vote" as const;
