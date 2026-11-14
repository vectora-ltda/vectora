/**
 * Arquivos e pastas que o sistema entrega ao Vectora ("Abrir com Vectora",
 * arrastar sobre o ícone, linha de comando): coleta dos argumentos e leitura
 * segura, com teto de tamanho, para a janela.
 */

import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** Teto do que o processo principal lê. O maior limite por tipo do app (vídeo). */
export const MAX_OPEN_FILE_BYTES = 50 * 1024 * 1024;

type OpenedPathError = "missing" | "unreadable" | "too_large" | "unsupported";

export type OpenedPath =
  | {
      kind: "file";
      path: string;
      name: string;
      mimeType: string;
      size: number;
      data: Uint8Array;
    }
  | { kind: "directory"; path: string; name: string }
  | { kind: "error"; path: string; name: string; reason: OpenedPathError };

/** Tipos MIME das extensões mais comuns; o restante a janela classifica pelo nome. */
const MIME_BY_EXTENSION: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  mp3: "audio/mpeg",
  wav: "audio/wav",
  m4a: "audio/mp4",
  ogg: "audio/ogg",
  oga: "audio/ogg",
  flac: "audio/flac",
  weba: "audio/webm",
  mp4: "video/mp4",
  webm: "video/webm",
  mov: "video/quicktime",
  m4v: "video/x-m4v",
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  odt: "application/vnd.oasis.opendocument.text",
  ods: "application/vnd.oasis.opendocument.spreadsheet",
  odp: "application/vnd.oasis.opendocument.presentation",
  txt: "text/plain",
  md: "text/markdown",
  csv: "text/csv",
  json: "application/json",
};

export function mimeTypeOf(fileName: string): string {
  const extension = path.extname(fileName).slice(1).toLowerCase();
  return MIME_BY_EXTENSION[extension] ?? "";
}

interface PathsFromArgvOptions {
  /** Diretório de trabalho de quem chamou (segunda instância); base de caminhos relativos. */
  cwd?: string;
  /** `electron .` em desenvolvimento: o segundo argumento é o app, não um arquivo. */
  defaultApp?: boolean;
}

/**
 * Caminhos de arquivo ou pasta entre os argumentos de linha de comando.
 *
 * Ignora o executável, o app em desenvolvimento, as opções (`--flag`) e URLs de
 * outros protocolos (`vectora://...`); aceita `file://` (o Linux entrega assim
 * com `%U`) e resolve caminhos relativos pelo `cwd`.
 */
export function pathsFromArgv(
  argv: readonly string[],
  options: PathsFromArgvOptions = {},
): string[] {
  const skip = options.defaultApp ? 2 : 1;
  const found: string[] = [];
  for (const raw of argv.slice(skip)) {
    if (!raw || raw.startsWith("-")) continue;
    let candidate = raw;
    if (/^file:\/\//i.test(raw)) {
      try {
        candidate = fileURLToPath(raw);
      } catch {
        continue;
      }
    } else if (/^[a-z][a-z0-9+.-]+:\/\//i.test(raw)) {
      continue;
    }
    const resolved = path.isAbsolute(candidate)
      ? candidate
      : path.resolve(options.cwd ?? process.cwd(), candidate);
    if (!found.includes(resolved)) found.push(resolved);
  }
  return found;
}

/** Lê um caminho recebido: arquivo (com os bytes), pasta ou o motivo da falha. */
export async function readOpenedPath(filePath: string): Promise<OpenedPath> {
  const name = path.basename(filePath) || filePath;
  let stats;
  try {
    stats = await fs.stat(filePath);
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    return {
      kind: "error",
      path: filePath,
      name,
      reason:
        code === "ENOENT" || code === "ENOTDIR" ? "missing" : "unreadable",
    };
  }
  if (stats.isDirectory()) return { kind: "directory", path: filePath, name };
  if (!stats.isFile()) {
    return { kind: "error", path: filePath, name, reason: "unsupported" };
  }
  if (stats.size > MAX_OPEN_FILE_BYTES) {
    return { kind: "error", path: filePath, name, reason: "too_large" };
  }
  try {
    const buffer = await fs.readFile(filePath);
    return {
      kind: "file",
      path: filePath,
      name,
      mimeType: mimeTypeOf(name),
      size: buffer.byteLength,
      data: new Uint8Array(buffer),
    };
  } catch {
    return { kind: "error", path: filePath, name, reason: "unreadable" };
  }
}

/** Para onde o despachante entrega os arquivos: a janela principal. */
export interface OpenPathsSink {
  /** `true` quando a janela existe e terminou de carregar. */
  canReceive: () => boolean;
  send: (items: OpenedPath[]) => void;
  /** Traz a janela para a frente depois de uma entrega. */
  focus: () => void;
}

/**
 * Guarda os caminhos que chegam antes de a janela poder recebê-los e os entrega
 * quando ela pede (`take`, ao montar). Depois do primeiro pedido, o que chega vai
 * direto à janela, por `send`, desde que ela esteja pronta.
 */
export function createOpenPathsDispatcher(
  sink: OpenPathsSink,
  read: (filePath: string) => Promise<OpenedPath> = readOpenedPath,
) {
  const pending: string[] = [];
  let windowAccepts = false;

  return {
    enqueue(paths: readonly string[]): void {
      if (paths.length === 0) return;
      if (windowAccepts && sink.canReceive()) {
        void Promise.all(paths.map((entry) => read(entry))).then((items) => {
          sink.send(items);
          sink.focus();
        });
      } else {
        pending.push(...paths);
      }
    },
    /** A janela pediu o que estava guardado; a partir daqui recebe por `send`. */
    async take(): Promise<OpenedPath[]> {
      windowAccepts = true;
      const paths = pending.splice(0);
      return Promise.all(paths.map((entry) => read(entry)));
    },
  };
}
