"use client";

/**
 * SearchTab — busca em filesystem do workspace.
 *
 * Input para busca + resultados com line preview.
 * Reutiliza SearchResultGroup do files-tab.
 */

import { useCallback, useState } from "react";
import { Loader2, Search, X } from "lucide-react";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { m } from "@/lib/paraglide/messages";

interface SearchHit {
  path: string;
  line: number;
  text: string;
}

interface SearchResult {
  file: string;
  hits: SearchHit[];
}

export function SearchTab() {
  const workspace = useWorkspacesStore((s) => s.getActive());
  const wsId = workspace?.id ?? "";

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSearch = useCallback(async () => {
    if (!query.trim() || !wsId) return;

    setIsSearching(true);
    setError(null);

    try {
      const qs = new URLSearchParams({ q: query.trim() });
      const res = await fetch(
        `/workspaces/${encodeURIComponent(wsId)}/fs/search?${qs}`,
      );
      if (!res.ok) throw new Error("Search failed");

      const data = (await res.json()) as { results: SearchResult[] };
      setResults(data.results || []);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : m.workbench_files_search_no_results(),
      );
    } finally {
      setIsSearching(false);
    }
  }, [query, wsId]);

  const handleClear = () => {
    setQuery("");
    setResults(null);
    setError(null);
  };

  return (
    <div className="h-full flex flex-col">
      {/* Search input */}
      <div className="border-b border-border/60 p-3">
        <div className="relative flex items-center">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <input
            type="text"
            placeholder={m.workbench_files_search_placeholder()}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleSearch();
            }}
            className="w-full bg-background text-sm pl-7 pr-8 py-1.5 border border-border/60 rounded-md outline-none focus:border-primary placeholder:text-muted-foreground"
          />
          {query && (
            <button
              onClick={handleClear}
              className="absolute right-2 text-muted-foreground hover:text-foreground"
              aria-label="Clear search"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Results */}
      <div className="flex-1 overflow-y-auto min-h-0 custom-scrollbar">
        {error && <div className="p-4 text-sm text-destructive">{error}</div>}

        {isSearching && (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {results && results.length === 0 && !isSearching && (
          <div className="p-4 text-sm text-muted-foreground text-center">
            {m.workbench_files_search_no_results()}
          </div>
        )}

        {results && results.length > 0 && (
          <div className="divide-y divide-border/40">
            {results.map((result) => (
              <SearchResultItem key={result.file} result={result} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SearchResultItem({ result }: { result: SearchResult }) {
  const [isExpanded, setIsExpanded] = useState(true);

  return (
    <div className="border-l-2 border-transparent hover:border-primary/40">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full p-2.5 text-left hover:bg-accent/40 transition-colors flex items-center gap-2"
      >
        <span className="text-[11px] font-medium text-foreground/60 flex-1 truncate">
          {result.file}
        </span>
        <span className="text-[10px] text-muted-foreground shrink-0">
          ({result.hits.length})
        </span>
      </button>

      {isExpanded && (
        <div className="px-4 py-1 space-y-1">
          {result.hits.slice(0, 3).map((hit, idx) => (
            <div
              key={idx}
              className="text-[10px] font-mono text-muted-foreground"
            >
              <span className="text-primary/60">{hit.line}:</span>{" "}
              <span className="truncate">{hit.text.trim()}</span>
            </div>
          ))}
          {result.hits.length > 3 && (
            <div className="text-[10px] text-muted-foreground/60">
              +{result.hits.length - 3} more
            </div>
          )}
        </div>
      )}
    </div>
  );
}
