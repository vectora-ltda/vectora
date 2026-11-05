"use client";

/**
 * File Preview Grid Component (F2 + F4)
 *
 * Cards de preview para arquivos anexados à mensagem:
 * - Imagens → thumbnail direto (object-cover)
 * - PDFs    → primeira página renderizada via pdfjs (F2)
 * - Código / texto → primeiras 4 linhas em monospace (F4)
 * - Outros  → ícone genérico + extensão + tamanho
 */

import { useEffect, useState } from "react";
import { m } from "@/lib/paraglide/messages";
import type { ImageAttachment } from "@/lib/types";
import { renderPdfFirstPage } from "@/lib/utils/files/pdf-preview";

// ============================================================================
// Helpers
// ============================================================================

const CODE_EXTENSIONS = new Set([
  "py",
  "js",
  "jsx",
  "ts",
  "tsx",
  "json",
  "md",
  "sh",
  "bash",
  "html",
  "css",
  "scss",
  "sql",
  "yaml",
  "yml",
  "toml",
  "rs",
  "go",
  "java",
  "c",
  "cpp",
  "h",
  "rb",
  "php",
  "swift",
  "kt",
  "scala",
  "r",
  "tf",
  "xml",
  "txt",
  "csv",
  "log",
]);

function extOf(filename: string): string {
  return filename.includes(".") ? filename.split(".").pop()!.toLowerCase() : "";
}

function isCodeFile(file: ImageAttachment): boolean {
  if (file.mimeType?.startsWith("image/")) return false;
  if (file.mimeType === "application/pdf") return false;
  if (file.mimeType?.startsWith("audio/")) return false;
  return CODE_EXTENSIONS.has(extOf(file.name ?? ""));
}

function isAudioFile(file: ImageAttachment): boolean {
  return Boolean(file.mimeType?.startsWith("audio/"));
}

/** Decodifica base64 → text (UTF-8 safe) */
function decodeBase64Text(b64: string): string {
  try {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
  } catch {
    return "";
  }
}

/** Primeiras N linhas não-vazias de um texto */
function firstLines(text: string, n = 4): string {
  return text
    .split("\n")
    .filter((l) => l.trim().length > 0)
    .slice(0, n)
    .join("\n");
}

// ============================================================================
// Sub-components
// ============================================================================

/** F2 — Thumbnail assíncrono para PDFs */
function PdfThumbnail({ file }: { file: ImageAttachment }) {
  const [thumbUrl, setThumbUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!file.base64) return;
    let cancelled = false;
    renderPdfFirstPage(file.base64)
      .then((url) => {
        if (!cancelled) setThumbUrl(url);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [file.base64]);

  const fileName = file.name ?? "document.pdf";

  if (thumbUrl) {
    return (
      <div className="relative h-full w-full">
        <img
          src={thumbUrl}
          alt={fileName}
          className="h-full w-full object-cover"
        />
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent px-1.5 py-1">
          <p className="text-xs text-white truncate" title={fileName}>
            {fileName}
          </p>
        </div>
      </div>
    );
  }

  // Loading / erro — mostra ícone PDF enquanto carrega
  const fileSizeKB = file.size ? Math.round(file.size / 1024) : 0;
  return (
    <div className="h-full flex flex-col items-center justify-center p-2 text-center">
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="w-8 h-8 mb-1.5 text-red-400"
      >
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="9" y1="13" x2="15" y2="13" />
        <line x1="9" y1="17" x2="15" y2="17" />
      </svg>
      <span
        className="text-[11px] font-medium text-foreground truncate w-full px-1 mb-0.5"
        title={fileName}
      >
        {fileName}
      </span>
      <div className="flex items-center gap-1">
        <span className="text-[10px] font-bold px-1 py-0.5 rounded bg-muted text-muted-foreground">
          {error ? "PDF" : "PDF…"}
        </span>
        {fileSizeKB > 0 && (
          <span className="text-[10px] text-muted-foreground">
            {fileSizeKB}KB
          </span>
        )}
      </div>
    </div>
  );
}

/** F4 — Preview de primeiras linhas para arquivos de código/texto */
function CodePreview({ file }: { file: ImageAttachment }) {
  const fileName = file.name ?? "file";
  const fileExt = extOf(fileName);
  const fileSizeKB = file.size ? Math.round(file.size / 1024) : 0;

  const snippet = file.base64 ? firstLines(decodeBase64Text(file.base64)) : "";

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header com nome e extensão */}
      <div className="flex items-center gap-1 px-2 pt-1.5 pb-0.5 shrink-0">
        <span className="text-[10px] font-bold px-1 py-0.5 rounded bg-muted text-muted-foreground">
          {fileExt.toUpperCase().slice(0, 4) || "TXT"}
        </span>
        <span
          className="text-[10px] text-foreground truncate flex-1"
          title={fileName}
        >
          {fileName}
        </span>
        {fileSizeKB > 0 && (
          <span className="text-[10px] text-muted-foreground shrink-0">
            {fileSizeKB}KB
          </span>
        )}
      </div>
      {/* Snippet de código */}
      {snippet ? (
        <pre className="flex-1 text-[9px] leading-[1.35] font-mono text-green-300/80 px-2 pb-1.5 overflow-hidden whitespace-pre-wrap break-all">
          {snippet}
        </pre>
      ) : (
        <div className="flex-1 flex items-center justify-center text-[10px] text-muted-foreground">
          vazio
        </div>
      )}
    </div>
  );
}

/** Preview de arquivo de áudio — ícone de nota musical + nome + tamanho */
function AudioChip({ file }: { file: ImageAttachment }) {
  const fileName = file.name ?? "audio";
  const fileSizeKB = file.size ? Math.round(file.size / 1024) : 0;

  return (
    <div className="h-full flex flex-col items-center justify-center p-2 text-center">
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="w-8 h-8 mb-1.5 text-purple-400"
      >
        <path d="M9 18V5l12-2v13" />
        <circle cx="6" cy="18" r="3" />
        <circle cx="18" cy="16" r="3" />
      </svg>
      <span
        className="text-[11px] font-medium text-foreground truncate w-full px-1 mb-0.5"
        title={fileName}
      >
        {fileName}
      </span>
      <div className="flex items-center gap-1">
        <span className="text-[10px] font-bold px-1 py-0.5 rounded bg-muted text-muted-foreground">
          {m.audio_attachment_badge()}
        </span>
        {fileSizeKB > 0 && (
          <span className="text-[10px] text-muted-foreground">
            {fileSizeKB}KB
          </span>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// FilePreviewCard
// ============================================================================

function FilePreviewCard({
  file,
  onRemove,
}: {
  file: ImageAttachment;
  onRemove: (id: string) => void;
}) {
  const isImage = file.mimeType?.startsWith("image/");
  const isPdf = file.mimeType === "application/pdf";
  const isAudio = isAudioFile(file);
  const isCode = isCodeFile(file);
  const fileName = file.name ?? "File";
  const fileExt = extOf(fileName);
  const fileSizeKB = file.size ? Math.round(file.size / 1024) : 0;

  return (
    <div className="group relative h-24 rounded-lg border border-border/60 hover:border-border bg-card/50 hover:bg-muted/50 transition-colors flex flex-col overflow-hidden">
      {isImage ? (
        // ── Imagem — thumbnail direto
        <div className="relative h-full w-full">
          <img
            src={file.url}
            alt={fileName}
            className="h-full w-full object-cover"
          />
          <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent px-1.5 py-1">
            <p className="text-xs text-white truncate" title={fileName}>
              {fileName}
            </p>
          </div>
        </div>
      ) : isPdf ? (
        // ── PDF — thumbnail via pdfjs (F2)
        <PdfThumbnail file={file} />
      ) : isAudio ? (
        // ── Áudio — ícone dedicado
        <AudioChip file={file} />
      ) : isCode ? (
        // ── Código/Texto — snippet das primeiras linhas (F4)
        <CodePreview file={file} />
      ) : (
        // ── Outros — ícone genérico
        <div className="h-full flex flex-col items-center justify-center p-2 text-center">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="w-8 h-8 mb-1.5 text-muted-foreground"
          >
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
          </svg>
          <span
            className="text-[11px] font-medium text-foreground truncate w-full px-1 mb-0.5"
            title={fileName}
          >
            {fileName}
          </span>
          <div className="flex items-center gap-1">
            <span className="text-[10px] font-bold px-1 py-0.5 rounded bg-muted text-muted-foreground">
              {fileExt.toUpperCase().slice(0, 4) || "FILE"}
            </span>
            {fileSizeKB > 0 && (
              <span className="text-[10px] text-muted-foreground">
                {fileSizeKB}KB
              </span>
            )}
          </div>
        </div>
      )}

      {/* Botão de remover — sempre visível no mobile, hover no desktop */}
      <button
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onRemove(file.id);
        }}
        className="absolute top-1 right-1 bg-black/60 hover:bg-black/80 text-white rounded-full w-5 h-5 flex items-center justify-center opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-all shadow-lg z-10 cursor-pointer"
        type="button"
        title="Remover arquivo"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="w-3 h-3"
        >
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
  );
}

// ============================================================================
// FilePreviewGrid
// ============================================================================

interface FilePreviewGridProps {
  files: ImageAttachment[];
  onRemove: (fileId: string) => void;
}

/**
 * Grid de cards de preview para arquivos anexados.
 *
 * @example
 * ```tsx
 * <FilePreviewGrid files={attachedFiles} onRemove={removeFile} />
 * ```
 */
export function FilePreviewGrid({ files, onRemove }: FilePreviewGridProps) {
  if (files.length === 0) return null;

  return (
    <div className="mb-2 grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2">
      {files.map((file) => (
        <FilePreviewCard key={file.id} file={file} onRemove={onRemove} />
      ))}
    </div>
  );
}
