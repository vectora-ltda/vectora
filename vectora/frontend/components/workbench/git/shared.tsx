"use client";

/** Peças compartilhadas entre as views do painel Git. */

import type {
  DiffFile,
  DiffHunk,
  DiffLine,
} from "@/lib/stores/workbench-store";
import { m } from "@/lib/paraglide/messages";

export const STATUS_TONE: Record<string, string> = {
  M: "text-git-modification",
  A: "text-git-addition",
  D: "text-destructive",
  R: "text-git-information",
  "?": "text-muted-foreground",
};

export function statusTone(status: string): string {
  return STATUS_TONE[status] ?? "text-muted-foreground";
}

function lineTone(type: DiffLine["type"]): string {
  if (type === "add") return "text-git-addition bg-git-addition/10";
  if (type === "delete") return "text-destructive bg-destructive/10";
  return "text-foreground/80";
}

function gutterTone(type: DiffLine["type"]): string {
  if (type === "add") return "bg-git-addition/20";
  if (type === "delete") return "bg-destructive/20";
  return "bg-muted/40";
}

/** Renderiza um hunk no mesmo modelo de duas colunas do GitHub Desktop. */
export function HunkView({ hunk }: { hunk: DiffHunk }) {
  return (
    <div className="overflow-x-auto rounded-sm bg-muted/30 font-mono text-[11px] leading-5">
      <div className="border-b border-border/50 px-2 py-1 text-sky-300/80">
        {hunk.header}
      </div>
      {hunk.lines.map((rawLine, i) => {
        const line: DiffLine =
          typeof rawLine === "string"
            ? {
                text: rawLine,
                type: rawLine.startsWith("+")
                  ? "add"
                  : rawLine.startsWith("-")
                    ? "delete"
                    : "context",
                old_line_number: null,
                new_line_number: null,
              }
            : rawLine;
        const sign =
          line.type === "add" ? "+" : line.type === "delete" ? "−" : " ";
        return (
          <div key={i}>
            <div
              className={`grid min-w-max grid-cols-[3.5rem_3.5rem_1.25rem_minmax(20rem,1fr)] ${lineTone(line.type)}`}
              data-diff-line={line.type}
            >
              <span
                className={`${gutterTone(line.type)} border-r border-border/40 px-2 text-right text-muted-foreground/70 select-none`}
              >
                {line.old_line_number ?? ""}
              </span>
              <span
                className={`${gutterTone(line.type)} border-r border-border/40 px-2 text-right text-muted-foreground/70 select-none`}
              >
                {line.new_line_number ?? ""}
              </span>
              <span className="px-1 text-center select-none">{sign}</span>
              <span className="whitespace-pre px-2">{line.text.slice(1)}</span>
            </div>
            {line.no_trailing_newline && (
              <div className="px-3 py-0.5 text-[10px] italic text-muted-foreground">
                {m.workbench_diff_no_trailing_newline()}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export type { DiffFile, DiffHunk, DiffLine };
