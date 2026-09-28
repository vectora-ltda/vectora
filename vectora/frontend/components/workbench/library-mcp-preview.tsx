import { ExternalLink, Puzzle } from "lucide-react";

import { m } from "@/lib/paraglide/messages";
import type { McpCanvasPreviewData } from "@/lib/stores/windows-store";

function safeHomepage(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.href
      : null;
  } catch {
    return null;
  }
}

function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.href
      : null;
  } catch {
    return null;
  }
}

/**
 * Renders an MCP catalog entry in the shared canvas. Homepage links are
 * limited to HTTP(S), credential names are shown without secret values, and
 * a neutral puzzle icon is used when the catalog has no image.
 */
export function LibraryMcpPreview({ mcp }: { mcp: McpCanvasPreviewData }) {
  const homepage = safeHomepage(mcp.homepage);
  const publisherUrl = safeExternalUrl(mcp.publisherUrl);

  return (
    <article className="h-full min-w-0 flex-1 overflow-y-auto bg-background">
      <header className="flex min-w-0 gap-5 border-b border-border/60 p-6">
        <div className="flex size-24 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-muted text-muted-foreground">
          {mcp.iconUrl ? (
            <img src={mcp.iconUrl} alt="" className="size-full object-cover" />
          ) : (
            <Puzzle className="size-10" />
          )}
        </div>
        <div className="min-w-0 space-y-2">
          <h1 className="text-2xl font-semibold text-foreground">{mcp.name}</h1>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            {mcp.publisher &&
              (publisherUrl ? (
                <a
                  href={publisherUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="hover:text-foreground"
                >
                  {m.library_mcp_preview_publisher({
                    publisher: mcp.publisher,
                  })}
                </a>
              ) : (
                <span>
                  {m.library_mcp_preview_publisher({
                    publisher: mcp.publisher,
                  })}
                </span>
              ))}
            {mcp.starsCount ? (
              <span>
                {m.library_mcp_preview_stars({ count: mcp.starsCount })}
              </span>
            ) : null}
            {mcp.downloadsCount ? (
              <span>
                {m.library_mcp_preview_downloads({
                  count: mcp.downloadsCount,
                })}
              </span>
            ) : null}
            {homepage && (
              <a
                href={homepage}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 hover:text-foreground"
              >
                {m.library_mcp_preview_homepage()}
                <ExternalLink className="size-3" />
              </a>
            )}
          </div>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            {mcp.description}
          </p>
        </div>
      </header>

      <div className="grid gap-6 p-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <section className="min-w-0 space-y-3">
          <h2 className="text-lg font-medium text-foreground">
            {m.library_mcp_preview_overview()}
          </h2>
          <p className="text-sm leading-6 text-muted-foreground">
            {mcp.description}
          </p>
        </section>

        <aside className="space-y-5 border-border/60 lg:border-l lg:pl-5">
          <section className="space-y-2">
            <h2 className="text-sm font-medium text-foreground">
              {m.library_mcp_preview_command()}
            </h2>
            <code className="block overflow-x-auto rounded-md bg-muted p-3 text-xs text-muted-foreground">
              {mcp.installCommand}
            </code>
          </section>
          <section className="space-y-2">
            <h2 className="text-sm font-medium text-foreground">
              {m.library_mcp_preview_credentials()}
            </h2>
            {mcp.envVars.length > 0 ? (
              <ul className="flex flex-wrap gap-1.5">
                {mcp.envVars.map((name) => (
                  <li
                    key={name}
                    className="rounded bg-muted px-2 py-1 font-mono text-xs text-muted-foreground"
                  >
                    {name}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">
                {m.library_mcp_preview_no_credentials()}
              </p>
            )}
          </section>
        </aside>
      </div>
    </article>
  );
}
