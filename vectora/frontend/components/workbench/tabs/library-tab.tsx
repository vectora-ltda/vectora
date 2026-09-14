"use client";

/**
 * LibraryTab — 3 seções fecháveis (MCP, Skills, Memory Library) com busca
 * e filtros toggle por categoria. A busca é client-side sobre os itens já
 * carregados de cada seção, sem endpoint agregado.
 */

import { Archive, Package, Puzzle, Search, Sparkles } from "lucide-react";
import { useCallback, useState } from "react";

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { m } from "@/lib/paraglide/messages";
import { McpSection } from "./library-mcp-section";
import { MemorySection } from "./library-memory-section";
import { SkillsSection } from "./library-skills-section";
import { ExtensionsSection } from "./library-extensions-section";

interface LibraryTabProps {
  threadId: string;
}

type LibraryFilter = "mcp" | "skills" | "memory" | "extensions";

const ALL_FILTERS: LibraryFilter[] = ["mcp", "skills", "memory", "extensions"];

/** Item genérico de qualquer seção — cada seção monta a lista completa a
 * partir do seu próprio backend; a busca/filtro aqui só precisa do nome
 * pra combinar contra a query. */
export interface LibraryItem {
  id: string;
  name: string;
  description?: string;
}

function FilterPill({
  label,
  active,
  onToggle,
}: {
  label: string;
  active: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={active}
      className={`text-xs px-2.5 py-1 rounded-full transition-colors ${
        active
          ? "bg-primary/15 text-primary"
          : "bg-muted text-muted-foreground hover:text-foreground hover:bg-muted/80"
      }`}
    >
      {label}
    </button>
  );
}

function LibrarySearchBox({
  query,
  onChange,
}: {
  query: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="relative min-w-0 flex-1">
      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
      <input
        type="text"
        value={query}
        onChange={(e) => onChange(e.target.value)}
        placeholder={m.library_search_placeholder()}
        className="w-full rounded-lg border border-border/60 bg-card/30 py-1.5 pl-7 pr-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary/50"
      />
    </div>
  );
}

function SectionEmptyState({ label }: { label: string }) {
  return (
    <p className="py-4 text-xs text-muted-foreground text-center">{label}</p>
  );
}

export function LibraryTab({ threadId }: LibraryTabProps) {
  void threadId;
  const [query, setQuery] = useState("");
  const [activeFilters, setActiveFilters] = useState<Set<LibraryFilter>>(
    new Set(ALL_FILTERS),
  );

  const toggleFilter = (filter: LibraryFilter) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(filter)) {
        next.delete(filter);
      } else {
        next.add(filter);
      }
      return next;
    });
  };

  // MCP, Skills e Memory vivem em subcomponentes próprios, que reportam a
  // contagem filtrada de volta via onCountChange.
  const [mcpCount, setMcpCount] = useState(0);
  const handleMcpCountChange = useCallback((count: number) => {
    setMcpCount(count);
  }, []);

  const [skillsCount, setSkillsCount] = useState(0);
  const handleSkillsCountChange = useCallback((count: number) => {
    setSkillsCount(count);
  }, []);

  const [memoryCount, setMemoryCount] = useState(0);
  const handleMemoryCountChange = useCallback((count: number) => {
    setMemoryCount(count);
  }, []);
  const [extensionCount, setExtensionCount] = useState(0);
  const handleExtensionCountChange = useCallback(
    (count: number) => setExtensionCount(count),
    [],
  );

  const noFiltersActive = activeFilters.size === 0;

  return (
    <div className="h-full flex flex-col">
      <div className="p-3 space-y-2 border-b border-border/60">
        <LibrarySearchBox query={query} onChange={setQuery} />
        <div className="flex flex-wrap gap-1.5">
          <FilterPill
            label={m.library_filter_mcp()}
            active={activeFilters.has("mcp")}
            onToggle={() => toggleFilter("mcp")}
          />
          <FilterPill
            label={m.library_filter_skills()}
            active={activeFilters.has("skills")}
            onToggle={() => toggleFilter("skills")}
          />
          <FilterPill
            label={m.library_filter_memory()}
            active={activeFilters.has("memory")}
            onToggle={() => toggleFilter("memory")}
          />
          <FilterPill
            label={m.library_filter_extensions()}
            active={activeFilters.has("extensions")}
            onToggle={() => toggleFilter("extensions")}
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">
        {noFiltersActive ? (
          <SectionEmptyState label={m.library_empty_no_filters()} />
        ) : (
          <Accordion
            type="multiple"
            defaultValue={["mcp", "skills", "memory"]}
            className="px-1"
          >
            {activeFilters.has("mcp") && (
              <AccordionItem value="mcp">
                <AccordionTrigger>
                  <span className="flex items-center gap-2 min-w-0">
                    <Puzzle className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate">
                      {m.library_section_mcp()} ({mcpCount})
                    </span>
                  </span>
                </AccordionTrigger>
                <AccordionContent>
                  <McpSection
                    query={query}
                    onCountChange={handleMcpCountChange}
                  />
                </AccordionContent>
              </AccordionItem>
            )}

            {activeFilters.has("skills") && (
              <AccordionItem value="skills">
                <AccordionTrigger>
                  <span className="flex items-center gap-2 min-w-0">
                    <Sparkles className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate">
                      {m.library_section_skills()} ({skillsCount})
                    </span>
                  </span>
                </AccordionTrigger>
                <AccordionContent>
                  <SkillsSection
                    query={query}
                    onCountChange={handleSkillsCountChange}
                  />
                </AccordionContent>
              </AccordionItem>
            )}

            {activeFilters.has("memory") && (
              <AccordionItem value="memory">
                <AccordionTrigger>
                  <span className="flex items-center gap-2 min-w-0">
                    <Archive className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate">
                      {m.library_section_memory()} ({memoryCount})
                    </span>
                  </span>
                </AccordionTrigger>
                <AccordionContent>
                  <MemorySection
                    query={query}
                    onCountChange={handleMemoryCountChange}
                  />
                </AccordionContent>
              </AccordionItem>
            )}
            {activeFilters.has("extensions") && (
              <AccordionItem value="extensions">
                <AccordionTrigger>
                  <span className="flex items-center gap-2 min-w-0">
                    <Package className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate">
                      {m.library_section_extensions()} ({extensionCount})
                    </span>
                  </span>
                </AccordionTrigger>
                <AccordionContent>
                  <ExtensionsSection
                    query={query}
                    onCountChange={handleExtensionCountChange}
                  />
                </AccordionContent>
              </AccordionItem>
            )}
          </Accordion>
        )}
      </div>
    </div>
  );
}
