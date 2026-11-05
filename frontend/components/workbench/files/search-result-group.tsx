import { useState } from "react";
import { ChevronRight } from "lucide-react";

import type { SearchHit } from "./files-api";

/** Grupo colapsável de resultados de busca por arquivo. */
export function SearchResultGroup({
  filePath,
  hits,
  onOpenHit,
}: {
  filePath: string;
  hits: SearchHit[];
  onOpenHit: (path: string, lineNumber: number) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const fileName = filePath.split("/").pop() ?? filePath;

  return (
    <div className="mb-0.5">
      <button
        onClick={() => setCollapsed((v) => !v)}
        className="flex items-center gap-1 w-full px-2 py-0.5 hover:bg-muted/40 rounded text-left"
      >
        <ChevronRight
          className={`w-3 h-3 text-muted-foreground shrink-0 transition-transform ${
            collapsed ? "" : "rotate-90"
          }`}
        />
        <span
          className="text-[11px] font-medium truncate flex-1"
          title={filePath}
        >
          {fileName}
        </span>
        <span className="text-[10px] text-muted-foreground shrink-0 tabular-nums">
          {hits.length}
        </span>
      </button>
      {!collapsed && (
        <div className="ml-4">
          {hits.map((hit, i) => (
            <button
              key={i}
              onClick={() => onOpenHit(hit.path, hit.line_number)}
              className="flex items-start gap-2 w-full px-2 py-px hover:bg-muted/40 rounded text-left"
            >
              <span className="text-[10px] text-muted-foreground shrink-0 w-7 text-right leading-relaxed tabular-nums">
                {hit.line_number}
              </span>
              <span className="text-[11px] font-mono text-foreground/80 truncate leading-relaxed">
                {hit.line_text.trimStart()}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
