"use client";

import { useEffect, useState } from "react";
import { ExternalLink, Loader2, X } from "lucide-react";

import { m } from "@/lib/paraglide/messages";

interface PreviewPayload {
  url: string;
  title: string | null;
  description: string | null;
  origin: string | null;
  image: string | null;
}

interface UrlPreviewCardProps {
  url: string;
  onDismiss: () => void;
}

type PreviewState =
  | { kind: "loading" }
  | { kind: "ready"; data: PreviewPayload }
  | { kind: "unavailable" };

export function UrlPreviewCard({
  url,
  onDismiss,
}: UrlPreviewCardProps): React.ReactElement {
  const [state, setState] = useState<PreviewState>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setState({ kind: "loading" });
      void fetch(`/url-preview?url=${encodeURIComponent(url)}`, {
        credentials: "include",
        signal: controller.signal,
      })
        .then(async (response) => {
          if (!response.ok) throw new Error("preview unavailable");
          return (await response.json()) as PreviewPayload;
        })
        .then((data) => setState({ kind: "ready", data }))
        .catch(() => {
          if (!controller.signal.aborted) setState({ kind: "unavailable" });
        });
    }, 350);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [url]);

  return (
    <div className="mb-2 rounded-md border border-border/60 bg-muted/20 p-2 text-xs">
      <div className="flex items-start gap-2">
        {state.kind === "loading" && (
          <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin" />
        )}
        {state.kind === "ready" && (
          <ExternalLink className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        )}
        <div className="min-w-0 flex-1">
          {state.kind === "loading" && (
            <span className="text-muted-foreground">
              {m.chat_url_preview_loading()}
            </span>
          )}
          {state.kind === "unavailable" && (
            <span className="text-muted-foreground">
              {m.chat_url_preview_unavailable()}
            </span>
          )}
          {state.kind === "ready" && (
            <>
              <p className="truncate font-medium">
                {state.data.title ?? state.data.origin ?? url}
              </p>
              {state.data.description && (
                <p className="mt-0.5 line-clamp-2 text-muted-foreground">
                  {state.data.description}
                </p>
              )}
              <p className="mt-0.5 truncate text-muted-foreground">
                {state.data.origin ?? url}
              </p>
            </>
          )}
        </div>
        <button
          type="button"
          className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
          onClick={onDismiss}
          aria-label={m.chat_url_preview_remove()}
          title={m.chat_url_preview_remove()}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
