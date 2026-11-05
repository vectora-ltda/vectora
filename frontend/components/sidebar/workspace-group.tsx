"use client";

import { memo } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ChevronDown, ChevronRight, Folder } from "lucide-react";
import type { Thread } from "@/lib/hooks/threads";
import type { WorkspaceInfo } from "@/lib/stores/workspaces-store";
import { m } from "@/lib/paraglide/messages";
import { MOTION_INSTANT, PANEL_TRANSITION } from "@/lib/motion/transitions";
import { shortWorkspaceName } from "./sidebar-utils";
import { ThreadItem } from "./thread-item";

interface WorkspaceGroupProps {
  workspace: WorkspaceInfo;
  threads: Thread[];
  isSearching: boolean;
  isCollapsed: boolean;
  currentThreadId: string;
  onToggle: (workspaceId: string) => void;
  onSelect: (threadId: string) => void;
  onDelete: (threadId: string, e: React.MouseEvent) => void;
  onRename: (threadId: string, title: string) => void;
  onTogglePin: (threadId: string, pinned: boolean) => void;
}

export const WorkspaceGroup = memo(function WorkspaceGroup({
  workspace,
  threads,
  isSearching,
  isCollapsed,
  currentThreadId,
  onToggle,
  onSelect,
  onDelete,
  onRename,
  onTogglePin,
}: WorkspaceGroupProps) {
  const reducedMotion = useReducedMotion();
  const expanded = isSearching || !isCollapsed;

  return (
    <div className="mt-4 px-3 first:mt-0">
      <button
        onClick={() => onToggle(workspace.id)}
        title={workspace.cwd}
        aria-expanded={expanded}
        aria-label={
          expanded
            ? m.sidebar_workspace_collapse()
            : m.sidebar_workspace_expand()
        }
        className="w-full flex items-center gap-1.5 px-3 py-0.5 mb-1 text-xs font-semibold text-sidebar-accent-foreground uppercase tracking-wider shadow-inset-light hover:text-foreground transition-colors rounded-md"
      >
        {expanded ? (
          <ChevronDown className="w-3 h-3 shrink-0" />
        ) : (
          <ChevronRight className="w-3 h-3 shrink-0" />
        )}
        <Folder className="w-3.5 h-3.5 shrink-0 text-muted-foreground normal-case" />
        <span className="truncate flex-1 text-left normal-case font-medium text-sidebar-foreground">
          {shortWorkspaceName(workspace)}
        </span>
        <span className="shrink-0 text-[10px] text-muted-foreground normal-case tracking-normal">
          {m.sidebar_workspace_thread_count({ n: threads.length })}
        </span>
      </button>
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={reducedMotion ? MOTION_INSTANT : PANEL_TRANSITION}
            className="overflow-hidden"
          >
            <div className="space-y-0.5 pl-2">
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
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
});
