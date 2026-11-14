"use client";

import { useMemo } from "react";

import { m } from "@/lib/paraglide/messages";
import type { SearchHit, SearchResult } from "./files-api";
import { SearchResultGroup } from "./search-result-group";

/** Hits de uma busca no conteúdo, agrupados por arquivo, na ordem em que
 *  chegaram. */
export function groupHitsByFile(hits: SearchHit[]): [string, SearchHit[]][] {
  const byFile = new Map<string, SearchHit[]>();
  for (const hit of hits) {
    const list = byFile.get(hit.path) ?? [];
    list.push(hit);
    byFile.set(hit.path, list);
  }
  return Array.from(byFile.entries());
}

/**
 * Resultado de uma busca no conteúdo do workspace: grupos colapsáveis por
 * arquivo, o aviso de "sem resultados" e o de resultado truncado. `null` (busca
 * ainda não feita) não mostra nada.
 */
export function SearchResultsList({
  result,
  onOpenHit,
}: {
  result: SearchResult | null;
  onOpenHit: (path: string, lineNumber: number) => void;
}) {
  const groups = useMemo(
    () => (result ? groupHitsByFile(result.hits) : []),
    [result],
  );
  if (result === null) return null;

  return (
    <div className="px-1">
      {result.hits.length === 0 && (
        <p className="text-[10px] text-muted-foreground text-center py-4">
          {m.workbench_files_search_no_results()}
        </p>
      )}
      {groups.map(([filePath, hits]) => (
        <SearchResultGroup
          key={filePath}
          filePath={filePath}
          hits={hits}
          onOpenHit={onOpenHit}
        />
      ))}
      {result.truncated && (
        <p className="text-[10px] text-muted-foreground px-2 py-1">
          {m.workbench_files_search_truncated()}
        </p>
      )}
    </div>
  );
}
