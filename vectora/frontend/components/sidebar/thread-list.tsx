"use client";

import { memo, useRef, useState } from "react";
import type { Thread } from "@/lib/hooks/threads";
import { m } from "@/lib/paraglide/messages";
import { ThreadListSkeleton } from "./thread-list-skeleton";
import { ThreadGroup } from "./thread-group";
import { WorkspaceGroup } from "./workspace-group";
import type { WorkspaceThreadGroup, GroupedThreads } from "./sidebar-utils";

interface ThreadListProps {
  isLoading: boolean;
  searchQuery: string;
  filteredThreads: Thread[];
  workspaceGroups: WorkspaceThreadGroup[];
  orphans: Thread[];
  grouped: GroupedThreads;
  currentThreadId: string;
  collapsedWorkspaces: Set<string>;
  isSearching: boolean;
  onSelectThread: (threadId: string) => void;
  onDeleteThread: (threadId: string, e: React.MouseEvent) => void;
  onRenameThread: (threadId: string, title: string) => void;
  onTogglePinThread: (threadId: string, pinned: boolean) => void;
  onToggleWorkspace: (workspaceId: string) => void;
  onRefresh?: () => Promise<unknown>;
}

export const ThreadList = memo(function ThreadList({
  isLoading,
  searchQuery,
  filteredThreads,
  workspaceGroups,
  orphans,
  grouped,
  currentThreadId,
  collapsedWorkspaces,
  isSearching,
  onSelectThread,
  onDeleteThread,
  onRenameThread,
  onTogglePinThread,
  onToggleWorkspace,
  onRefresh,
}: ThreadListProps) {
  const [pullDistance, setPullDistance] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const refreshingRef = useRef(false);
  const startY = useRef<number | null>(null);
  const navRef = useRef<HTMLElement>(null);
  const onTouchStart = (event: React.TouchEvent<HTMLElement>) => {
    if (event.touches.length === 1 && navRef.current?.scrollTop === 0) {
      startY.current = event.touches[0].clientY;
    }
  };
  const onTouchMove = (event: React.TouchEvent<HTMLElement>) => {
    if (startY.current === null || event.touches.length !== 1) return;
    const distance = event.touches[0].clientY - startY.current;
    if (distance > 0) setPullDistance(Math.min(distance, 80));
    else startY.current = null;
  };
  const onTouchEnd = async () => {
    const shouldRefresh =
      pullDistance >= 56 && !refreshingRef.current && onRefresh;
    startY.current = null;
    setPullDistance(0);
    if (!shouldRefresh) return;
    refreshingRef.current = true;
    setRefreshing(true);
    try {
      await onRefresh();
    } finally {
      refreshingRef.current = false;
      setRefreshing(false);
    }
  };
  const onTouchCancel = () => {
    startY.current = null;
    setPullDistance(0);
  };
  const { today, yesterday, last7Days, older } = grouped;

  return (
    <>
      <nav
        ref={navRef}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        onTouchCancel={onTouchCancel}
        aria-busy={refreshing}
        className="flex-1 overflow-y-auto py-2 bg-gradient-to-b from-sidebar-accent/5 via-transparent to-sidebar-accent/10 custom-scrollbar"
      >
        {(pullDistance > 0 || refreshing) && (
          <div
            className="text-center text-[11px] text-muted-foreground"
            role="status"
          >
            {refreshing ? m.sidebar_refreshing() : m.sidebar_pull_to_refresh()}
          </div>
        )}
        {isLoading ? (
          <ThreadListSkeleton />
        ) : searchQuery && filteredThreads.length === 0 ? (
          <div className="px-6 py-8 text-center text-sm text-muted-foreground bg-gradient-to-br from-card/10 via-card/5 to-transparent rounded-lg mx-3 shadow-depth-xs">
            <div className="font-medium mb-1">{m.sidebar_no_results()}</div>
            <div className="text-xs">{m.sidebar_no_results_hint()}</div>
          </div>
        ) : filteredThreads.length === 0 ? (
          <div className="px-6 py-8 text-center text-sm text-muted-foreground bg-gradient-to-br from-card/10 via-card/5 to-transparent rounded-lg mx-3 shadow-depth-xs">
            <div className="font-medium mb-1">
              {m.sidebar_no_conversations()}
            </div>
            <div className="text-xs">{m.sidebar_no_conversations_hint()}</div>
          </div>
        ) : (
          <>
            {workspaceGroups.map((group) => (
              <WorkspaceGroup
                key={`wsg-${group.workspace.id}`}
                workspace={group.workspace}
                threads={group.threads}
                isSearching={isSearching}
                isCollapsed={collapsedWorkspaces.has(group.workspace.id)}
                currentThreadId={currentThreadId}
                onToggle={onToggleWorkspace}
                onSelect={onSelectThread}
                onDelete={onDeleteThread}
                onRename={onRenameThread}
                onTogglePin={onTogglePinThread}
              />
            ))}

            {orphans.length > 0 && (
              <>
                {workspaceGroups.length > 0 && (
                  <h3 className="mt-4 px-6 text-xs font-semibold text-sidebar-accent-foreground/70 uppercase tracking-wider shadow-inset-light">
                    {m.sidebar_group_other_conversations()}
                  </h3>
                )}
                <ThreadGroup
                  threads={today}
                  label={m.sidebar_group_today()}
                  currentThreadId={currentThreadId}
                  onSelect={onSelectThread}
                  onDelete={onDeleteThread}
                  onRename={onRenameThread}
                  onTogglePin={onTogglePinThread}
                />
                <ThreadGroup
                  threads={yesterday}
                  label={m.sidebar_group_yesterday()}
                  currentThreadId={currentThreadId}
                  onSelect={onSelectThread}
                  onDelete={onDeleteThread}
                  onRename={onRenameThread}
                  onTogglePin={onTogglePinThread}
                />
                <ThreadGroup
                  threads={last7Days}
                  label={m.sidebar_group_last_7_days()}
                  currentThreadId={currentThreadId}
                  onSelect={onSelectThread}
                  onDelete={onDeleteThread}
                  onRename={onRenameThread}
                  onTogglePin={onTogglePinThread}
                />
                <ThreadGroup
                  threads={older}
                  label={m.sidebar_group_older()}
                  currentThreadId={currentThreadId}
                  onSelect={onSelectThread}
                  onDelete={onDeleteThread}
                  onRename={onRenameThread}
                  onTogglePin={onTogglePinThread}
                />
              </>
            )}
          </>
        )}
      </nav>
    </>
  );
});
