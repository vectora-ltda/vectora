"use client";

/** Peças compartilhadas entre as views do painel Git. */

import type { DiffFile, DiffHunk } from "@/lib/stores/workbench-store";

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

/** Render de um hunk unificado (verde/+, vermelho/−). Scroll horizontal próprio. */
export function HunkView({ hunk }: { hunk: DiffHunk }) {
  return (
    <pre className="text-[11px] font-mono leading-tight bg-muted/30 rounded-sm px-2 py-1 overflow-x-auto">
      {hunk.lines.map((line, i) => {
        const tone = line.startsWith("+")
          ? "text-git-addition"
          : line.startsWith("-")
            ? "text-destructive"
            : "text-foreground/80";
        return (
          <span key={i} className={tone}>
            {line}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}

export type { DiffFile, DiffHunk };
