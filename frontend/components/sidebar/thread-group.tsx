"use client";

import { memo } from "react";
import type { Thread } from "@/lib/hooks/threads";
import { ThreadItem } from "./thread-item";

interface ThreadGroupProps {
  threads: Thread[];
  label: string;
  currentThreadId: string;
  onSelect: (threadId: string) => void;
  onDelete: (threadId: string, e: React.MouseEvent) => void;
  onRename: (threadId: string, title: string) => void;
  onTogglePin: (threadId: string, pinned: boolean) => void;
}

export const ThreadGroup = memo(function ThreadGroup({
  threads,
  label,
  currentThreadId,
  onSelect,
  onDelete,
  onRename,
  onTogglePin,
}: ThreadGroupProps) {
  if (threads.length === 0) return null;

  return (
    <div className="mt-2 first:mt-0">
      <h3 className="px-4 text-[10px] font-medium text-muted-foreground/50 uppercase tracking-wider mb-0">
        {label}
      </h3>
      <div className="px-2 space-y-0.5">
        {threads.map((thread) => (
          <ThreadItem
            key={thread.thread_id}
            thread={thread}
            isActive={thread.thread_id === currentThreadId}
            onSelect={onSelect}
            onDelete={onDelete}
            onRename={onRename}
            onTogglePin={onTogglePin}
          />
        ))}
      </div>
    </div>
  );
});
