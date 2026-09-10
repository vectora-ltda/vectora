"use client";

import { memo, useEffect, useRef, useState } from "react";
import { Pin, Trash2 } from "lucide-react";
import type { Thread } from "@/lib/hooks/threads";
import { queryClient } from "../../src/router";
import {
  getHistory,
  listThreads,
  markThreadRead,
} from "@/lib/api/vectora-client";
import { threadsQueryKey } from "@/lib/queries/threads";
import { THREAD_FETCH_LIMIT } from "@/lib/constants/features";
import { m } from "@/lib/paraglide/messages";
import { useStreamingStore } from "@/lib/stores/streaming-store";
import { useContextMenu } from "@/components/workbench/git/git-context-menu";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";

interface ThreadItemProps {
  thread: Thread;
  isActive: boolean;
  onSelect: (threadId: string) => void;
  onDelete: (threadId: string, e: React.MouseEvent) => void;
  onRename: (threadId: string, title: string) => void;
  onTogglePin: (threadId: string, pinned: boolean) => void;
}

export const ThreadItem = memo(function ThreadItem({
  thread,
  isActive,
  onSelect,
  onDelete,
  onRename,
  onTogglePin,
}: ThreadItemProps) {
  const title = thread.metadata?.title || m.sidebar_new_conversation();
  const isStreaming = useStreamingStore((s) =>
    Boolean(s.streaming[thread.thread_id]),
  );
  const menu = useContextMenu();
  const [isEditing, setIsEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
  const [actionsOpen, setActionsOpen] = useState(false);
  const longPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const suppressClick = useRef(false);
  const touchStart = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    if (!isActive || !thread.unread_count) return;
    void markThreadRead(thread.thread_id)
      .then(() => {
        queryClient.setQueryData<{
          threads: { id: string; unread_count?: number }[];
        }>(threadsQueryKey(), (data) =>
          data
            ? {
                threads: data.threads.map((item) =>
                  item.id === thread.thread_id
                    ? { ...item, unread_count: 0 }
                    : item,
                ),
              }
            : data,
        );
      })
      .catch(() => {
        void queryClient.invalidateQueries({ queryKey: threadsQueryKey() });
      });
  }, [isActive, thread.thread_id, thread.unread_count]);

  const cancelLongPress = () => {
    if (longPressTimer.current) clearTimeout(longPressTimer.current);
    longPressTimer.current = null;
  };
  const handleTouchStart = (event: React.TouchEvent<HTMLDivElement>) => {
    if (event.touches.length !== 1 || isEditing) return;
    const target = event.target as HTMLElement;
    if (target.closest("button, input, [role=button]")) return;
    touchStart.current = {
      x: event.touches[0].clientX ?? 0,
      y: event.touches[0].clientY,
    };
    longPressTimer.current = setTimeout(() => {
      suppressClick.current = true;
      setActionsOpen(true);
    }, 500);
  };
  const handleTouchMove = (event: React.TouchEvent<HTMLDivElement>) => {
    const initial = touchStart.current;
    if (!initial || event.touches.length !== 1) return;
    const touch = event.touches[0];
    const distance = Math.hypot(
      (touch.clientX ?? 0) - initial.x,
      touch.clientY - initial.y,
    );
    if (distance > 10) cancelLongPress();
  };
  const handleTouchEnd = () => {
    touchStart.current = null;
    cancelLongPress();
  };

  const handleMouseEnter = () => {
    void queryClient.prefetchQuery({
      queryKey: ["thread-history", thread.thread_id],
      queryFn: () => getHistory(thread.thread_id),
      staleTime: 30_000,
    });
    void queryClient.prefetchQuery({
      // Mesmo limit que useThreadsQuery/os loaders de rota — um valor
      // diferente aqui sob a mesma chave (agora parametrizada por limit)
      // simplesmente vira uma entrada de cache separada, sem colidir, mas
      // também sem aproveitar o cache principal. Manter consistente.
      queryKey: threadsQueryKey(),
      queryFn: () => listThreads(THREAD_FETCH_LIMIT),
      staleTime: 30_000,
    });
  };

  const startEditing = () => {
    setDraftTitle(title);
    setIsEditing(true);
  };

  const commitRename = () => {
    const trimmed = draftTitle.trim();
    if (trimmed && trimmed !== title) onRename(thread.thread_id, trimmed);
    setIsEditing(false);
  };

  const cancelRename = () => setIsEditing(false);

  const handleContextMenu = (e: React.MouseEvent) => {
    menu.open(e, [
      { label: m.sidebar_ctx_rename(), onSelect: startEditing },
      {
        label: thread.pinned ? m.sidebar_ctx_unpin() : m.sidebar_ctx_pin(),
        onSelect: () => onTogglePin(thread.thread_id, !thread.pinned),
      },
      {
        label: m.sidebar_ctx_delete(),
        danger: true,
        onSelect: () =>
          onDelete(thread.thread_id, e as unknown as React.MouseEvent),
      },
    ]);
  };

  const handleSelect = () => {
    // Limpa a indicação imediatamente e confirma no backend. Em caso de
    // falha, a invalidação restaura a contagem real na próxima consulta.
    queryClient.setQueryData<{
      threads: { id: string; unread_count?: number }[];
    }>(threadsQueryKey(), (data) =>
      data
        ? {
            threads: data.threads.map((item) =>
              item.id === thread.thread_id
                ? { ...item, unread_count: 0 }
                : item,
            ),
          }
        : data,
    );
    void markThreadRead(thread.thread_id).catch(() => {
      void queryClient.invalidateQueries({ queryKey: threadsQueryKey() });
    });
    onSelect(thread.thread_id);
  };

  if (isEditing) {
    return (
      <div
        className={`flex items-center gap-2 px-2 py-1 text-sm w-full rounded-md ${
          isActive ? "bg-muted/60" : ""
        }`}
      >
        <input
          autoFocus
          value={draftTitle}
          placeholder={m.sidebar_rename_placeholder()}
          onChange={(e) => setDraftTitle(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commitRename();
            } else if (e.key === "Escape") {
              e.preventDefault();
              cancelRename();
            }
          }}
          className="w-full min-w-0 flex-1 bg-background/80 border border-border/40 rounded px-1.5 py-0.5 text-[12px] leading-5 text-foreground focus:outline-none focus:border-primary/60"
        />
      </div>
    );
  }

  return (
    <div
      className={`group flex items-center gap-2 px-2 py-1 text-sm w-full rounded-md transition-colors duration-150 cursor-pointer ${
        isActive
          ? "bg-muted/60 text-foreground"
          : "text-muted-foreground hover:bg-muted/30 hover:text-foreground"
      }`}
      onClick={() => {
        if (suppressClick.current) {
          suppressClick.current = false;
          return;
        }
        handleSelect();
      }}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
      onTouchCancel={handleTouchEnd}
      onMouseEnter={handleMouseEnter}
      onContextMenu={handleContextMenu}
    >
      <div className="flex items-center gap-2 flex-1 min-w-0">
        {isStreaming ? (
          <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-foreground/50 animate-pulse" />
        ) : (
          <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-transparent" />
        )}
        {thread.pinned && (
          <Pin className="shrink-0 w-3 h-3 text-muted-foreground/70 fill-current" />
        )}
        <span className="truncate text-[12px] leading-5">{title}</span>
        {thread.unread_count && thread.unread_count > 0 ? (
          <span
            className="shrink-0 rounded-full bg-primary px-1.5 text-[10px] leading-4 text-primary-foreground"
            aria-label={m.unread_messages_count({ n: thread.unread_count })}
          >
            {thread.unread_count > 99 ? "99+" : thread.unread_count}
          </span>
        ) : null}
      </div>
      <button
        onClick={(e) => onDelete(thread.thread_id, e)}
        aria-label={m.sidebar_delete_thread()}
        // opacity-100 abaixo de md: sem :hover em touch, o botão precisa
        // ficar sempre visível pra ser alcançável; acima de md: continua
        // reveal-on-hover, mas com focus-visible pra navegação via teclado.
        className="opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100 transition-opacity duration-150 p-1 rounded hover:bg-destructive/10 shrink-0"
      >
        <Trash2 className="w-3 h-3 text-muted-foreground hover:text-destructive" />
      </button>
      {menu.element}
      <Sheet open={actionsOpen} onOpenChange={setActionsOpen}>
        <SheetContent side="bottom" className="safe-area-bottom p-4">
          <SheetTitle className="text-sm">{title}</SheetTitle>
          <SheetDescription className="sr-only">
            {m.sidebar_ctx_rename()}
          </SheetDescription>
          <div className="grid gap-2">
            <button
              type="button"
              className="rounded-md p-3 text-left hover:bg-muted"
              onClick={() => {
                startEditing();
                setActionsOpen(false);
              }}
            >
              {m.sidebar_ctx_rename()}
            </button>
            <button
              type="button"
              className="rounded-md p-3 text-left hover:bg-muted"
              onClick={() => {
                onTogglePin(thread.thread_id, !thread.pinned);
                setActionsOpen(false);
              }}
            >
              {thread.pinned ? m.sidebar_ctx_unpin() : m.sidebar_ctx_pin()}
            </button>
            <button
              type="button"
              className="rounded-md p-3 text-left text-destructive hover:bg-destructive/10"
              onClick={() => {
                onDelete(thread.thread_id, {
                  stopPropagation() {},
                } as React.MouseEvent);
                setActionsOpen(false);
              }}
            >
              {m.sidebar_ctx_delete()}
            </button>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
});
