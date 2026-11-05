import { GIT_BADGE_TONE } from "./files-utils";

/** Badge de status git de um arquivo (M/A/D/R/?) derivado do diff porcelain. */
export function GitBadge({ status }: { status?: string }) {
  if (!status) return null;
  return (
    <span
      className={`w-3 text-center font-bold shrink-0 text-[10px] ${
        GIT_BADGE_TONE[status] ?? "text-muted-foreground"
      }`}
      title={status}
    >
      {status}
    </span>
  );
}
