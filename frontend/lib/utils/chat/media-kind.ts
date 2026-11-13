/**
 * Tipo de anexo no frontend — espelho de `backend/services/media_types.py`.
 *
 * Aqui o tipo serve para o limite de tamanho e para a prévia. O backend
 * reclassifica pelos bytes (assinatura do arquivo) e é quem decide como o
 * anexo chega ao modelo; nenhum tipo de arquivo é recusado pelo formato.
 */

export type MediaKind =
  "image" | "audio" | "video" | "document" | "text" | "file";

const MB = 1024 * 1024;

/** Teto por tipo, igual ao `MAX_BYTES_BY_KIND` do backend. */
export const MAX_BYTES_BY_KIND: Record<MediaKind, number> = {
  image: 10 * MB,
  audio: 25 * MB,
  video: 50 * MB,
  document: 25 * MB,
  text: 10 * MB,
  file: 25 * MB,
};
export const MAX_HAR_BYTES = 50 * MB;

const IMAGE_MIMES = new Set([
  "image/png",
  "image/jpeg",
  "image/jpg",
  "image/gif",
  "image/webp",
]);
const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".webp"];
const AUDIO_EXTENSIONS = [
  ".mp3",
  ".wav",
  ".m4a",
  ".ogg",
  ".oga",
  ".flac",
  ".weba",
];
const VIDEO_EXTENSIONS = [".mp4", ".webm", ".mov", ".m4v"];
const DOCUMENT_EXTENSIONS = [
  ".pdf",
  ".docx",
  ".xlsx",
  ".pptx",
  ".odt",
  ".ods",
  ".odp",
];
const DOCUMENT_MIMES = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.oasis.opendocument.text",
  "application/vnd.oasis.opendocument.spreadsheet",
  "application/vnd.oasis.opendocument.presentation",
]);
const TEXT_EXTENSIONS = [
  ".txt",
  ".md",
  ".log",
  ".csv",
  ".tsv",
  ".json",
  ".jsonl",
  ".har",
  ".yaml",
  ".yml",
  ".toml",
  ".ini",
  ".xml",
  ".html",
  ".css",
  ".svg",
  ".sql",
  ".graphql",
  ".py",
  ".js",
  ".jsx",
  ".ts",
  ".tsx",
  ".java",
  ".c",
  ".h",
  ".cpp",
  ".cs",
  ".go",
  ".rs",
  ".rb",
  ".php",
  ".sh",
  ".bash",
  ".r",
  ".swift",
  ".kt",
  ".scala",
  ".lua",
  ".dart",
  ".vue",
  ".svelte",
  ".env.example",
  ".gitignore",
  ".dockerfile",
];
const TEXT_MIMES = new Set([
  "application/json",
  "application/xml",
  "application/javascript",
  "application/typescript",
  "application/x-sh",
  "application/x-python",
  "application/x-python-code",
  "application/x-yaml",
  "application/toml",
]);

const hasExtension = (name: string, extensions: string[]): boolean => {
  const lowered = name.toLowerCase();
  return extensions.some((ext) => lowered.endsWith(ext));
};

/** Tipo provável do arquivo pelo MIME do navegador e pela extensão. */
export function mediaKindOf(mimeType: string, name: string): MediaKind {
  const mime = mimeType.toLowerCase();
  if (IMAGE_MIMES.has(mime) || (!mime && hasExtension(name, IMAGE_EXTENSIONS)))
    return "image";
  if (DOCUMENT_MIMES.has(mime) || hasExtension(name, DOCUMENT_EXTENSIONS))
    return "document";
  if (mime.startsWith("audio/") || hasExtension(name, AUDIO_EXTENSIONS))
    return "audio";
  if (mime.startsWith("video/") || hasExtension(name, VIDEO_EXTENSIONS))
    return "video";
  if (
    mime.startsWith("text/") ||
    TEXT_MIMES.has(mime) ||
    hasExtension(name, TEXT_EXTENSIONS)
  ) {
    return "text";
  }
  return "file";
}

/** Teto em bytes do arquivo, com o teto próprio de HAR. */
export function maxBytesFor(kind: MediaKind, name: string): number {
  if (kind === "text" && name.toLowerCase().endsWith(".har"))
    return MAX_HAR_BYTES;
  return MAX_BYTES_BY_KIND[kind];
}

/** Arquivos lidos como texto no navegador (contagem de caracteres). */
export const isTextKind = (kind: MediaKind): boolean => kind === "text";
