"use client";

/**
 * Viewer de arquivo compartilhado entre o painel docked (files-tab) e as
 * janelas flutuantes (windows). Concentra a detecção de mídia e o render de
 * imagem/vídeo/áudio/pdf a partir do endpoint de bytes crus
 * `GET /workspaces/{id}/fs/raw`. Texto é lido do `GET /file` (truncado).
 */

import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { Loader2, Pencil } from "lucide-react";
import { MarkdownView } from "@/components/workbench/markdown-view";
import { useToastStore } from "@/lib/stores/toast-store";
import { m } from "@/lib/paraglide/messages";

// Monaco depende de `window` — carregado sob demanda (lazy) para não entrar no
// grafo de import estático do viewer (quebraria testes/SSR sem DOM).
const MonacoReadOnly = lazy(
  () => import("@/components/workbench/monaco-readonly"),
);

/** Verdadeiro para arquivos markdown (render GitHub no viewer). */
function isMarkdown(path: string): boolean {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return ext === "md" || ext === "markdown";
}

export type MediaKind = "image" | "video" | "audio" | "pdf";

const MEDIA_BY_EXT: Record<string, MediaKind> = {
  png: "image",
  jpg: "image",
  jpeg: "image",
  gif: "image",
  webp: "image",
  avif: "image",
  bmp: "image",
  ico: "image",
  svg: "image",
  mp4: "video",
  webm: "video",
  mov: "video",
  mkv: "video",
  m4v: "video",
  ogv: "video",
  mp3: "audio",
  wav: "audio",
  ogg: "audio",
  flac: "audio",
  m4a: "audio",
  aac: "audio",
  pdf: "pdf",
};

/** Tipo de mídia do path (por extensão), ou null para texto/binário comum. */
export function getMediaKind(path: string): MediaKind | null {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return MEDIA_BY_EXT[ext] ?? null;
}

/** URL same-origin dos bytes crus do arquivo (preview de mídia + download). */
export function rawFileUrl(workspaceId: string, path: string): string {
  const qs = new URLSearchParams({ path });
  return `/workspaces/${encodeURIComponent(workspaceId)}/fs/raw?${qs}`;
}

/** Render de mídia (imagem/vídeo/áudio/pdf) a partir do endpoint raw. */
export function MediaView({
  kind,
  workspaceId,
  path,
}: {
  kind: MediaKind;
  workspaceId: string;
  path: string;
}) {
  const src = rawFileUrl(workspaceId, path);
  if (kind === "image") {
    return (
      <div className="flex items-center justify-center h-full w-full overflow-auto p-2">
        <img
          src={src}
          alt={path}
          className="max-w-full max-h-full object-contain"
        />
      </div>
    );
  }
  if (kind === "video") {
    return (
      <div className="flex items-center justify-center h-full w-full bg-black/40">
        <video src={src} controls className="max-w-full max-h-full" />
      </div>
    );
  }
  if (kind === "audio") {
    return (
      <div className="flex items-center justify-center h-full w-full p-4">
        <audio src={src} controls className="w-full max-w-xl" />
      </div>
    );
  }
  // pdf — render nativo do browser via <object> (sem iframe).
  return (
    <object
      data={src}
      type="application/pdf"
      className="w-full h-full"
      aria-label={path}
    >
      <a href={src} className="text-primary hover:underline">
        {path}
      </a>
    </object>
  );
}

interface RawText {
  kind: "text" | "binary";
  content?: string;
  size: number;
  truncated?: boolean;
}

function InlineTextEditor({
  workspaceId,
  path,
  initialContent,
  onSaved,
}: {
  workspaceId: string;
  path: string;
  initialContent: string;
  onSaved: (content: string) => void;
}) {
  const [content, setContent] = useState(initialContent);
  const [saving, setSaving] = useState(false);
  const showError = useToastStore((s) => s.error);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const resp = await fetch(
        `/workspaces/${encodeURIComponent(workspaceId)}/file`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ path, content }),
        },
      );
      if (resp.ok) {
        onSaved(content);
      } else {
        showError(m.workbench_files_save_error());
      }
    } catch {
      showError(m.workbench_files_save_error());
    } finally {
      setSaving(false);
    }
  }, [workspaceId, path, content, onSaved, showError]);

  return (
    <div className="flex h-full flex-col">
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        className="flex-1 resize-none p-3 font-mono text-xs bg-background text-foreground focus:outline-none"
        spellCheck={false}
      />
      <div className="shrink-0 flex justify-end gap-2 border-t border-border/60 px-3 py-1.5 bg-muted/30">
        <button
          onClick={() => void handleSave()}
          disabled={saving}
          className="text-xs px-2 py-0.5 rounded bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {saving ? (
            <Loader2 className="w-3 h-3 animate-spin inline" />
          ) : (
            m.workbench_files_save()
          )}
        </button>
      </div>
    </div>
  );
}

/**
 * Viewer completo e autônomo — usado pelas janelas flutuantes.
 * Decide entre mídia (raw) e texto (busca o conteúdo do `GET /file`).
 */
export function FileViewer({
  workspaceId,
  path,
}: {
  workspaceId: string;
  path: string;
}) {
  const media = getMediaKind(path);
  const [text, setText] = useState<RawText | null>(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);

  const handleEdit = useCallback(() => setEditing(true), [setEditing]);
  const handleCancelEdit = useCallback(() => setEditing(false), [setEditing]);

  useEffect(() => {
    if (media) return; // mídia não busca conteúdo de texto
    let cancelled = false;
    // Busca o conteúdo do arquivo no backend (rede) ao trocar de path.
    // oxlint-disable-next-line react/set-state-in-effect
    setLoading(true);
    const qs = new URLSearchParams({ path });
    fetch(`/workspaces/${encodeURIComponent(workspaceId)}/file?${qs}`, {
      credentials: "include",
    })
      .then((r) => (r.ok ? (r.json() as Promise<RawText>) : null))
      .then((data) => {
        if (!cancelled) setText(data);
      })
      .catch(() => {
        if (!cancelled) setText(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, path, media]);

  if (media) {
    return <MediaView kind={media} workspaceId={workspaceId} path={path} />;
  }
  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (text?.kind === "binary") {
    return (
      <div className="p-3 text-xs text-muted-foreground">
        {m.workbench_files_binary({ size: text.size })}{" "}
        <a
          href={rawFileUrl(workspaceId, path)}
          download
          className="text-primary hover:underline"
        >
          {m.workbench_files_download()}
        </a>
      </div>
    );
  }
  if (isMarkdown(path) && text?.content) {
    return (
      <div className="h-full overflow-auto custom-scrollbar">
        <MarkdownView content={text.content} />
        {text.truncated && (
          <p className="px-4 pb-2 text-[10px] text-muted-foreground">
            {m.workbench_files_read_only_truncated()}
          </p>
        )}
      </div>
    );
  }

  if (editing) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between border-b border-border/60 px-3 py-1.5 bg-muted/30 shrink-0">
          <span className="text-xs font-mono text-muted-foreground truncate">
            {path}
          </span>
          <button
            onClick={handleCancelEdit}
            className="text-xs px-2 py-0.5 rounded text-muted-foreground hover:text-foreground transition-colors"
          >
            {m.workbench_files_cancel()}
          </button>
        </div>
        <InlineTextEditor
          workspaceId={workspaceId}
          path={path}
          initialContent={text?.content ?? ""}
          onSaved={(content) => {
            setText((prev) => (prev ? { ...prev, content } : prev));
            setEditing(false);
          }}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border/60 px-3 py-1.5 bg-muted/30 shrink-0">
        <span className="text-xs font-mono text-muted-foreground truncate">
          {path}
        </span>
        <button
          onClick={handleEdit}
          className="p-0.5 rounded text-muted-foreground hover:text-foreground transition-colors"
          title={m.workbench_files_edit()}
        >
          <Pencil className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1">
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          }
        >
          <MonacoReadOnly value={text?.content ?? ""} path={path} />
        </Suspense>
      </div>
      {text?.truncated && (
        <p className="shrink-0 border-t border-border/60 px-2 py-1 text-[10px] text-muted-foreground">
          {m.workbench_files_read_only_truncated()}
        </p>
      )}
    </div>
  );
}
