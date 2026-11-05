import { Loader2 } from "lucide-react";

import { fmtDate } from "./files-utils";
import type { FileLogEntry } from "./files-api";

/** Lista de commits que tocaram um arquivo (git log/file). */
export function FileHistoryPanel({
  entries,
  loading,
  selectedSha,
  onSelectSha,
}: {
  entries: FileLogEntry[] | null;
  loading: boolean;
  selectedSha: string | null;
  onSelectSha: (sha: string) => void;
}) {
  if (loading) {
    return (
      <div className="flex justify-center py-6">
        <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (!entries || entries.length === 0) {
    return (
      <p className="text-[10px] text-muted-foreground text-center py-6 px-4">
        Nenhum commit encontrado para este arquivo.
      </p>
    );
  }
  return (
    <div className="px-1 py-1">
      {entries.map((entry) => (
        <button
          key={entry.sha}
          onClick={() => onSelectSha(entry.sha)}
          className={`w-full text-left px-2 py-1.5 rounded mb-0.5 hover:bg-muted/40 transition-colors ${
            selectedSha === entry.sha ? "bg-primary/10 hover:bg-primary/15" : ""
          }`}
        >
          <div className="flex items-center gap-1.5 mb-0.5">
            <span className="text-[10px] font-mono text-primary shrink-0">
              {entry.sha_short}
            </span>
            <span className="text-[10px] text-muted-foreground shrink-0">
              {fmtDate(entry.date)}
            </span>
          </div>
          <p className="text-[11px] text-foreground/90 truncate leading-tight">
            {entry.message}
          </p>
          <p className="text-[10px] text-muted-foreground truncate mt-0.5">
            {entry.author.split("<")[0].trim()}
          </p>
        </button>
      ))}
    </div>
  );
}
