"use client";

import { memo, useState } from "react";
import { Pin, Trash2 } from "lucide-react";
import type { Thread } from "@/lib/hooks/threads";
import { queryClient } from "../../src/router";
import { getHistory, listThreads } from "@/lib/api/vectora-client";
import { threadsQueryKey } from "@/lib/queries/threads";
import { THREAD_FETCH_LIMIT } from "@/lib/constants/features";
import { m } from "@/lib/paraglide/messages";
import { useStreamingStore } from "@/lib/stores/streaming-store";
import { useContextMenu } from "@/components/workbench/git/git-context-menu";
import { getRelativeTime } from "@/components/sidebar/sidebar-utils";

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
  const remoteActivity = thread.remote_activity?.last_active_at
    ? getRelativeTime(new Date(thread.remote_activity.last_active_at))
    : null;
  const menu = useContextMenu();
  const [isEditing, setIsEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);

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
      ...(remoteActivity
        ? [
            {
              label: m.sidebar_ctx_resume(),
              onSelect: () => onSelect(thread.thread_id),
            },
          ]
        : []),
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
      onClick={() => onSelect(thread.thread_id)}
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
        {remoteActivity && (
          <button
            type="button"
            className="shrink-0 w-3 h-3 rounded-full bg-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
            title={m.sidebar_remote_activity_tooltip({ time: remoteActivity })}
            aria-label={m.sidebar_ctx_resume()}
            onClick={(e) => {
              e.stopPropagation();
              onSelect(thread.thread_id);
            }}
          />
        )}
        <span className="truncate text-[12px] leading-5">{title}</span>
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
    </div>
  );
});
