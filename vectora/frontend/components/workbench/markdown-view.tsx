"use client";

/**
 * MarkdownView — render de markdown estilo GitHub (read-only).
 *
 * Reutilizado pelo visualizador embarcado (`file-viewer` para `.md`) e pelo
 * diálogo de preview. Usa react-markdown + remark-gfm (tabelas, task lists,
 * strikethrough) com as classes `prose` do Tailwind Typography.
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function MarkdownView({
  content,
  assetBaseUrl,
}: {
  content: string;
  assetBaseUrl?: string;
}) {
  const transformUrl = (url: string) => {
    if (!assetBaseUrl || /^(?:[a-z]+:|\/\/|#)/i.test(url)) return url;
    return `${assetBaseUrl.replace(/\/$/, "")}/${url
      .split("/")
      .map((segment) => encodeURIComponent(segment))
      .join("/")}`;
  };
  return (
    <div
      className="prose prose-sm dark:prose-invert max-w-none p-4 prose-pre:bg-muted prose-pre:text-foreground prose-code:before:content-none prose-code:after:content-none prose-h1:text-lg prose-h2:text-base prose-h3:text-sm prose-h4:text-sm"
      style={{ fontSize: "calc(0.875rem * var(--font-scale-markdown, 1))" }}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={transformUrl}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
