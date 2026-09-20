"use client";

/**
 * LibraryTab — 3 seções fecháveis (MCP, Skills, Memory Library) com busca
 * e seleção única de categoria. A busca é client-side sobre os itens já
 * carregados de cada seção, sem endpoint agregado.
 */

import { Archive, Puzzle, Search, Sparkles } from "lucide-react";
import { useCallback, useState, type KeyboardEvent } from "react";

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
const LIBRARY_SECTIONS = ["mcp", "skills", "memory"] as const;

/** Item genérico de qualquer seção — cada seção monta a lista completa a
 * partir do seu próprio backend; a busca/filtro aqui só precisa do nome
 * pra combinar contra a query. */
export interface LibraryItem {
  id: string;
  name: string;
  description?: string;
}

function FilterPill({
  value,
  label,
  active,
  onSelect,
  onKeyDown,
  tabIndex,
}: {
  label: string;
  value: string;
  active: boolean;
  onSelect: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
  tabIndex: number;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      aria-controls={`library-panel-${value}`}
      id={`library-tab-${value}`}
      tabIndex={tabIndex}
      onClick={onSelect}
      onKeyDown={onKeyDown}
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

export function LibraryTab({ threadId }: LibraryTabProps) {
  void threadId;
  const [query, setQuery] = useState("");
  const [activeSection, setActiveSection] = useState<LibraryFilter>();

  // MCP, Skills e Memory vivem em subcomponentes próprios, que reportam a
  // contagem atualizada de volta via onCountChange.
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

  const handleSectionKeyDown = useCallback(
    (
      event: KeyboardEvent<HTMLButtonElement>,
      value: (typeof LIBRARY_SECTIONS)[number],
    ) => {
      if (
        ![
          "ArrowRight",
          "ArrowDown",
          "ArrowLeft",
          "ArrowUp",
          "Home",
          "End",
        ].includes(event.key)
      ) {
        return;
      }
      event.preventDefault();
      const index = LIBRARY_SECTIONS.indexOf(value);
      const nextIndex =
        event.key === "Home"
          ? 0
          : event.key === "End"
            ? LIBRARY_SECTIONS.length - 1
            : (index +
                (event.key === "ArrowLeft" || event.key === "ArrowUp"
                  ? -1
                  : 1) +
                LIBRARY_SECTIONS.length) %
              LIBRARY_SECTIONS.length;
      const next = document.getElementById(
        `library-tab-${LIBRARY_SECTIONS[nextIndex]}`,
      );
      if (next instanceof HTMLButtonElement) {
        next.focus();
        setActiveSection(LIBRARY_SECTIONS[nextIndex]);
      }
    },
    [],
  );

  return (
    <div className="flex h-full flex-col bg-[#181818]">
      <div className="flex min-h-[86px] flex-col gap-2.5 border-b border-[#2a2a2a]/60 p-2.5">
        <LibrarySearchBox query={query} onChange={setQuery} />
        <div
          className="flex min-w-0 flex-wrap items-start gap-2.5"
          role="tablist"
        >
          <FilterPill
            value="mcp"
            label={m.library_filter_mcp()}
            active={activeSection === "mcp"}
            onSelect={() => setActiveSection("mcp")}
            onKeyDown={(event) => handleSectionKeyDown(event, "mcp")}
            tabIndex={
              activeSection === "mcp" || activeSection === undefined ? 0 : -1
            }
          />
          <FilterPill
            value="skills"
            label={m.library_filter_skills()}
            active={activeSection === "skills"}
            onSelect={() => setActiveSection("skills")}
            onKeyDown={(event) => handleSectionKeyDown(event, "skills")}
            tabIndex={activeSection === "skills" ? 0 : -1}
          />
          <FilterPill
            value="memory"
            label={m.library_filter_memory()}
            active={activeSection === "memory"}
            onSelect={() => setActiveSection("memory")}
            onKeyDown={(event) => handleSectionKeyDown(event, "memory")}
            tabIndex={activeSection === "memory" ? 0 : -1}
          />
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <Accordion
          type="single"
          collapsible
          value={activeSection ?? ""}
          onValueChange={(value) =>
            setActiveSection((value || undefined) as LibraryFilter | undefined)
          }
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <AccordionItem
            value="mcp"
            className="shrink-0 border-b-0 data-[state=open]:flex data-[state=open]:min-h-0 data-[state=open]:flex-1 data-[state=open]:flex-col"
          >
            <AccordionTrigger className="min-h-[49px] gap-2 px-2.5 py-3.5 hover:no-underline">
              <span className="flex min-w-0 items-center gap-2">
                <Puzzle className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate text-sm leading-5">
                  {m.library_section_mcp()} ({mcpCount})
                </span>
              </span>
            </AccordionTrigger>
            <AccordionContent
              id="library-panel-mcp"
              role="tabpanel"
              aria-labelledby="library-tab-mcp"
              containerClassName="data-[state=open]:flex data-[state=open]:min-h-0 data-[state=open]:flex-1 data-[state=open]:overflow-hidden"
              className="h-full overflow-y-auto pb-2 pl-2 pr-1"
            >
              <McpSection
                query={query}
                onCountChange={handleMcpCountChange}
                threadId={threadId}
              />
            </AccordionContent>
          </AccordionItem>

          <AccordionItem
            value="skills"
            className="shrink-0 border-b-0 data-[state=open]:flex data-[state=open]:min-h-0 data-[state=open]:flex-1 data-[state=open]:flex-col"
          >
            <AccordionTrigger className="min-h-[49px] gap-2 px-2.5 py-3.5 hover:no-underline">
              <span className="flex min-w-0 items-center gap-2">
                <Sparkles className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate text-sm leading-5">
                  {m.library_section_skills()} ({skillsCount})
                </span>
              </span>
            </AccordionTrigger>
            <AccordionContent
              id="library-panel-skills"
              role="tabpanel"
              aria-labelledby="library-tab-skills"
              containerClassName="data-[state=open]:flex data-[state=open]:min-h-0 data-[state=open]:flex-1 data-[state=open]:overflow-hidden"
              className="h-full overflow-y-auto pb-2 pl-2 pr-1"
            >
              <SkillsSection
                query={query}
                onCountChange={handleSkillsCountChange}
              />
            </AccordionContent>
          </AccordionItem>

          <AccordionItem
            value="memory"
            className="shrink-0 border-b-0 data-[state=open]:flex data-[state=open]:min-h-0 data-[state=open]:flex-1 data-[state=open]:flex-col"
          >
            <AccordionTrigger className="min-h-[49px] gap-2 px-2.5 py-3.5 hover:no-underline">
              <span className="flex min-w-0 items-center gap-2">
                <Archive className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate text-sm leading-5">
                  {m.library_section_memory()} ({memoryCount})
                </span>
              </span>
            </AccordionTrigger>
            <AccordionContent
              id="library-panel-memory"
              role="tabpanel"
              aria-labelledby="library-tab-memory"
              containerClassName="data-[state=open]:flex data-[state=open]:min-h-0 data-[state=open]:flex-1 data-[state=open]:overflow-hidden"
              className="h-full overflow-y-auto pb-2 pl-2 pr-1"
            >
              <MemorySection
                query={query}
                onCountChange={handleMemoryCountChange}
              />
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </div>
    </div>
  );
}
