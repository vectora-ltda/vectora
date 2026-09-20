"use client";

/**
 * LibraryTab — 3 seções fecháveis (MCP, Skills, Memory Library) com busca
 * e filtros toggle por categoria. A busca é client-side sobre os itens já
 * carregados de cada seção, sem endpoint agregado.
 */

import { Archive, Puzzle, Search, Sparkles } from "lucide-react";
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

interface LibraryTabProps {
  threadId: string;
}

type LibraryFilter = "mcp" | "skills" | "memory";

const ALL_FILTERS: LibraryFilter[] = ["mcp", "skills", "memory"];

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
      className={`h-6 rounded-[5px] px-2.5 py-1 text-xs transition-colors ${
        active
          ? "bg-[#d4d4d4]/15 text-[#d4d4d4]"
          : "bg-muted text-muted-foreground hover:bg-muted/80 hover:text-foreground"
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
        className="h-8 w-full rounded-md border border-[#2a2a2a]/60 bg-[#252525]/30 py-2 pl-8 pr-2.5 text-xs text-foreground placeholder:text-muted-foreground shadow-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
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

  const noFiltersActive = activeFilters.size === 0;

  return (
    <div className="flex h-full flex-col bg-[#181818]">
      <div className="flex min-h-[86px] flex-col gap-2.5 border-b border-[#2a2a2a]/60 p-2.5">
        <LibrarySearchBox query={query} onChange={setQuery} />
        <div className="flex min-w-0 flex-wrap items-start gap-2.5">
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
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {noFiltersActive ? (
          <SectionEmptyState label={m.library_empty_no_filters()} />
        ) : (
          <Accordion type="multiple" defaultValue={[]} className="px-0">
            {activeFilters.has("mcp") && (
              <AccordionItem value="mcp" className="border-b-0">
                <AccordionTrigger className="min-h-[49px] gap-2 px-2.5 py-3.5 hover:no-underline">
                  <span className="flex min-w-0 items-center gap-2">
                    <Puzzle className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate text-sm leading-5">
                      {m.library_section_mcp()} ({mcpCount})
                    </span>
                  </span>
                </AccordionTrigger>
                <AccordionContent className="px-1.5 pb-2">
                  <McpSection
                    query={query}
                    onCountChange={handleMcpCountChange}
                  />
                </AccordionContent>
              </AccordionItem>
            )}

            {activeFilters.has("skills") && (
              <AccordionItem value="skills" className="border-b-0">
                <AccordionTrigger className="min-h-[49px] gap-2 px-2.5 py-3.5 hover:no-underline">
                  <span className="flex min-w-0 items-center gap-2">
                    <Sparkles className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate text-sm leading-5">
                      {m.library_section_skills()} ({skillsCount})
                    </span>
                  </span>
                </AccordionTrigger>
                <AccordionContent className="px-1.5 pb-2">
                  <SkillsSection
                    query={query}
                    onCountChange={handleSkillsCountChange}
                  />
                </AccordionContent>
              </AccordionItem>
            )}

            {activeFilters.has("memory") && (
              <AccordionItem value="memory" className="border-b-0">
                <AccordionTrigger className="min-h-[49px] gap-2 px-2.5 py-3.5 hover:no-underline">
                  <span className="flex min-w-0 items-center gap-2">
                    <Archive className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate text-sm leading-5">
                      {m.library_section_memory()} ({memoryCount})
                    </span>
                  </span>
                </AccordionTrigger>
                <AccordionContent className="px-1.5 pb-2">
                  <MemorySection
                    query={query}
                    onCountChange={handleMemoryCountChange}
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
