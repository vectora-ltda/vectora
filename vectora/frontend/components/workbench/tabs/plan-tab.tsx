"use client";

/**
 * PlanTab — lista unificada de artifacts + checklist ao vivo (write_todos)
 * da sessão, num Accordion multi-item (vários abertos ao mesmo tempo,
 * expandindo inline — sem faixa fixa separada no rodapé).
 *
 * Estado vive no workbench-store (slice `plan`):
 *   - lista de artifacts da sessão → cacheada por threadId
 *   - slugs abertos no Accordion + conteúdo carregado por slug → idem
 * SWR via `useWorkbenchSWR`.
 */

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { MarkdownView } from "@/components/workbench/markdown-view";
import {
  Boxes,
  BookOpen,
  CheckCircle2,
  CheckSquare,
  Circle,
  CircleDot,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Code2,
  FileCode2,
  FileText,
  LayoutList,
  Loader2,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef } from "react";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useWorkbenchSWR } from "@/lib/hooks/workbench/use-swr";
import { useChatInputStore } from "@/lib/stores/chat-input-store";
import { getThreadActivity } from "@/lib/api/vectora-client";
import {
  WORKBENCH_STALE_MS,
  useWorkbenchStore,
  type PlanItem,
  type TodoItem,
} from "@/lib/stores/workbench-store";
import { m } from "@/lib/paraglide/messages";

async function fetchArtifacts(threadId: string): Promise<PlanItem[]> {
  const qs = new URLSearchParams({ session_id: threadId });
  const res = await fetch(`/artifacts/?${qs}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.artifacts ?? [];
}

async function fetchArtifactContent(
  threadId: string,
  slug: string,
): Promise<string | null> {
  const qs = new URLSearchParams({ session_id: threadId });
  const res = await fetch(`/artifacts/${encodeURIComponent(slug)}?${qs}`);
  if (!res.ok) return null;
  const data = await res.json();
  return data.content ?? null;
}

function fileSlug(path: string): string {
  const last = path.split(/[/\\]/).pop() ?? "";
  return last.replace(/\.md$/i, "");
}

// Ícone + cor por artifact_type — mesmo padrão de `components/icons/`.
// Tipo desconhecido/ausente (artifact legado sem sidecar) cai no fallback.
const TYPE_ICON: Record<string, { icon: LucideIcon; className: string }> = {
  plan: { icon: ClipboardList, className: "text-blue-400" },
  spec: { icon: FileCode2, className: "text-purple-400" },
  task_list: { icon: CheckSquare, className: "text-amber-400" },
  overview: { icon: LayoutList, className: "text-sky-400" },
  guide: { icon: BookOpen, className: "text-green-400" },
  architecture: { icon: Boxes, className: "text-orange-400" },
  implementation: { icon: Code2, className: "text-pink-400" },
  skill_learned: { icon: Sparkles, className: "text-fuchsia-400" },
  fact_learned: { icon: Sparkles, className: "text-fuchsia-400" },
  remember_proposal: { icon: Sparkles, className: "text-fuchsia-300" },
};
const DEFAULT_TYPE_ICON = { icon: FileText, className: "text-primary" };

function artifactIcon(artifactType: string | undefined) {
  return (artifactType && TYPE_ICON[artifactType]) || DEFAULT_TYPE_ICON;
}

const TODOS_SLUG = "__todos__";

interface PlanTabProps {
  threadId: string;
  onOpenPlanDocument?: (item: PlanItem, content: string | null) => void;
}

function FilesTouchedSection({ threadId }: { threadId: string }) {
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["thread-activity", threadId],
    queryFn: () => getThreadActivity(threadId),
    staleTime: 60_000,
  });
  const files = data?.files_touched ?? [];
  if (files.length === 0) return null;
  return (
    <div className="border-t border-border/40 shrink-0">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-1.5 px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        {open ? (
          <ChevronDown className="w-3 h-3 shrink-0" />
        ) : (
          <ChevronRight className="w-3 h-3 shrink-0" />
        )}
        <span className="font-medium flex-1 text-left">
          {m.workbench_plan_files_touched()} ({files.length})
        </span>
      </button>
      {open && (
        <div className="px-2 pb-2 space-y-0.5">
          {files.map((f) => (
            <p
              key={f}
              className="text-[10px] font-mono text-muted-foreground truncate pl-4"
            >
              {f}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function TodoStatusIcon({ status }: { status: TodoItem["status"] }) {
  switch (status) {
    case "completed":
      return (
        <CheckCircle2 className="w-3 h-3 shrink-0 text-primary" aria-hidden />
      );
    case "in_progress":
      return (
        <CircleDot className="w-3 h-3 shrink-0 text-amber-500" aria-hidden />
      );
    default:
      return (
        <Circle
          className="w-3 h-3 shrink-0 text-muted-foreground/50"
          aria-hidden
        />
      );
  }
}

export function PlanTab({ threadId, onOpenPlanDocument }: PlanTabProps) {
  const contentRequestEpoch = useRef(new Map<string, number>());
  useEffect(() => {
    if (!threadId) return;
    contentRequestEpoch.current.clear();
  }, [threadId]);
  const items = useWorkbenchStore((s) => s.getPlan(threadId).items);
  const todos = useWorkbenchStore((s) => s.getTodos(threadId));
  const fetchedAt = useWorkbenchStore((s) => s.getPlan(threadId).fetchedAt);
  const openSlugs = useWorkbenchStore((s) => s.getPlan(threadId).openSlugs);
  const contentsBySlug = useWorkbenchStore(
    (s) => s.getPlan(threadId).contentsBySlug,
  );

  const setPlanItems = useWorkbenchStore((s) => s.setPlanItems);
  const togglePlanOpenSlug = useWorkbenchStore((s) => s.togglePlanOpenSlug);
  const setPlanContent = useWorkbenchStore((s) => s.setPlanContent);

  const isPlanStale = useCallback(
    () => Date.now() - fetchedAt > WORKBENCH_STALE_MS,
    [fetchedAt],
  );

  useWorkbenchSWR({
    key: `plan:${threadId}`,
    hasCache: fetchedAt > 0,
    isStale: isPlanStale,
    revalidate: async () => {
      const list = await fetchArtifacts(threadId);
      setPlanItems(threadId, list);
    },
  });

  // write_todos (TodoListMiddleware) vira UMA entrada sintética no
  // Accordion (não um item por tarefa) — sempre a mais recente, já que é o
  // progresso ao vivo do turno atual, não um documento datado como os
  // artifacts.
  const entries = useMemo(() => {
    const artifactEntries = items.map((item) => ({
      slug: fileSlug(item.path),
      timestamp: new Date(item.created_at).getTime() || 0,
      item,
    }));
    // O checklist ao vivo é sempre o progresso do turno atual — ordena
    // primeiro sem depender do relógio (Infinity vence qualquer created_at).
    const todosEntry =
      todos.length > 0
        ? [
            {
              slug: TODOS_SLUG,
              timestamp: Number.POSITIVE_INFINITY,
              item: null,
            },
          ]
        : [];
    return [...todosEntry, ...artifactEntries].toSorted(
      (a, b) => b.timestamp - a.timestamp,
    );
  }, [items, todos]);

  const handleAccordionChange = useCallback(
    (next: string[]) => {
      const added = next.filter((v) => !openSlugs.includes(v));
      const removed = openSlugs.filter((v) => !next.includes(v));
      for (const slug of [...added, ...removed]) {
        togglePlanOpenSlug(threadId, slug);
      }
      for (const slug of added) {
        if (slug === TODOS_SLUG) continue;
        const item = items.find(
          (candidate) => fileSlug(candidate.path) === slug,
        );
        if (contentsBySlug[slug] !== undefined) {
          if (item) onOpenPlanDocument?.(item, contentsBySlug[slug]);
          continue;
        }
        const requestEpoch = (contentRequestEpoch.current.get(slug) ?? 0) + 1;
        contentRequestEpoch.current.set(slug, requestEpoch);
        void fetchArtifactContent(threadId, slug).then((content) => {
          if (requestEpoch !== contentRequestEpoch.current.get(slug)) return;
          if (content !== null) setPlanContent(threadId, slug, content);
          if (item) onOpenPlanDocument?.(item, content);
        });
        if (item) onOpenPlanDocument?.(item, contentsBySlug[slug] ?? null);
      }
    },
    [
      openSlugs,
      threadId,
      togglePlanOpenSlug,
      contentsBySlug,
      setPlanContent,
      items,
      onOpenPlanDocument,
    ],
  );

  // Estado de loading inicial: ainda não fetchamos uma única vez.
  const initialLoading = fetchedAt === 0;

  if (initialLoading) {
    return (
      <div className="h-full flex items-center justify-center">
        <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // "Sem planos" só quando NÃO há artifact salvo NEM checklist do write_todos.
  // Plan Mode gera o plano via write_todos (todos), sem create_artifact — olhar
  // só `items` (artifacts) escondia o plano inteiro atrás do empty-state.
  if (items.length === 0 && todos.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3 p-4 text-center">
        <FileText className="w-6 h-6 text-muted-foreground/40" />
        <p className="text-xs text-muted-foreground">
          {m.workbench_plan_empty()}
        </p>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs gap-1.5"
          onClick={() =>
            useChatInputStore
              .getState()
              .pushDraft(m.workbench_plan_ask_prompt())
          }
        >
          <Sparkles className="w-3 h-3" />
          {m.workbench_plan_ask_cta()}
        </Button>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 overflow-y-auto min-h-0">
        <Accordion
          type="multiple"
          value={openSlugs}
          onValueChange={handleAccordionChange}
          className="px-1"
        >
          {entries.map(({ slug, item }) =>
            slug === TODOS_SLUG ? (
              <AccordionItem key={slug} value={slug}>
                <AccordionTrigger data-testid="plan-todos-trigger">
                  <span className="flex items-center gap-2 min-w-0">
                    <CheckSquare className="w-3.5 h-3.5 shrink-0 text-amber-400" />
                    <span className="truncate">
                      {m.workbench_plan_tasks_section()} ({todos.length})
                    </span>
                  </span>
                </AccordionTrigger>
                <AccordionContent>
                  <div
                    className="divide-y divide-border/30"
                    data-testid="plan-todos-list"
                  >
                    {todos.map((t, idx) => (
                      <div
                        key={idx}
                        data-testid="plan-todo-item"
                        className="py-1.5 text-[11px] flex items-center gap-2"
                      >
                        <TodoStatusIcon status={t.status} />
                        <span
                          className={
                            t.status === "completed"
                              ? "line-through text-foreground/40"
                              : "text-foreground/70"
                          }
                        >
                          {t.content}
                        </span>
                      </div>
                    ))}
                  </div>
                </AccordionContent>
              </AccordionItem>
            ) : (
              item && (
                <AccordionItem key={slug} value={slug}>
                  <AccordionTrigger>
                    {(() => {
                      const { icon: Icon, className } = artifactIcon(
                        item.artifact_type,
                      );
                      return (
                        <span className="flex items-center gap-2 min-w-0">
                          <Icon
                            className={`w-3.5 h-3.5 shrink-0 mt-0.5 self-start ${className}`}
                          />
                          <span className="whitespace-normal break-words">
                            {item.title}
                          </span>
                        </span>
                      );
                    })()}
                  </AccordionTrigger>
                  <AccordionContent>
                    {contentsBySlug[slug] === undefined ? (
                      <Loader2 className="w-4 h-4 animate-spin text-muted-foreground m-2" />
                    ) : (
                      <MarkdownView content={contentsBySlug[slug]} />
                    )}
                  </AccordionContent>
                </AccordionItem>
              )
            ),
          )}
        </Accordion>
      </div>

      <FilesTouchedSection threadId={threadId} />
    </div>
  );
}
