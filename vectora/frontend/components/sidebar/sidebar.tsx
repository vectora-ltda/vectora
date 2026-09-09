"use client";

import { useState, useMemo, memo, useCallback } from "react";
import { AnimatePresence, motion } from "motion/react";
import { PANEL_TRANSITION } from "@/lib/motion/transitions";
import type { Thread } from "@/lib/hooks/threads";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { useRagJobsStore } from "@/lib/stores/rag-jobs-store";
import { useWebhookEvents } from "@/lib/hooks/use-webhook-events";
import { useUpdateThread } from "@/lib/queries/threads";
import { groupThreads, groupThreadsByWorkspace } from "./sidebar-utils";
import { CollapsedSidebar } from "./collapsed-sidebar";
import { SidebarHeader } from "./sidebar-header";
import { NewChatButton } from "./new-chat-button";
import { SessionSearch } from "./session-search";
import { SidebarModeToggle } from "./sidebar-mode-toggle";
import { ThreadList } from "./thread-list";
import { SidebarFooter } from "./sidebar-footer";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { m } from "@/lib/paraglide/messages";

interface SidebarProps {
  isCollapsed: boolean;
  onToggle: () => void;
  threads: Thread[];
  currentThreadId: string;
  onSelectThread: (threadId: string) => void;
  onDeleteThread: (threadId: string) => void;
  onNewChat?: () => void;
  isLoading?: boolean;
  /** true quando a sessão atual é nova/vazia — destaca "Nova sessão". */
  isNewSession?: boolean;
  onRefreshThreads?: () => Promise<unknown>;
}

export const Sidebar = memo(function Sidebar({
  isCollapsed,
  onToggle,
  threads,
  currentThreadId,
  onSelectThread,
  onDeleteThread,
  onNewChat,
  isLoading = false,
  isNewSession = false,
  onRefreshThreads,
}: SidebarProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [collapsedWorkspaces, setCollapsedWorkspaces] = useState<Set<string>>(
    () => new Set(),
  );
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  const workspaces = useWorkspacesStore((s) => s.workspaces);
  const chatMode = useSettingsStore((s) => s.chatMode);
  const ragJobs = useRagJobsStore((s) => s.jobs);
  const applyRagEvent = useRagJobsStore((s) => s.applyEvent);
  const updateThreadMutation = useUpdateThread();

  // Progresso de indexação RAG chega via SSE (bridge cross-réplica de
  // webhooks.py) em vez de depender só do polling de baixa frequência.
  const onWebhook = useCallback(
    (evt: { provider: string; data: Record<string, unknown> }) => {
      if (evt.provider === "rag") {
        applyRagEvent(
          evt.data as unknown as Parameters<typeof applyRagEvent>[0],
        );
      }
    },
    [applyRagEvent],
  );
  useWebhookEvents(onWebhook);

  // Chat e Dev são pools separados: a sidebar mostra só as sessões do modo ativo.
  // Sessões legadas sem modo são tratadas como "dev".
  const modeThreads = useMemo(() => {
    // Pools separados: chat vs código. O backend normaliza o modo em
    // "chat"|"code" (_normalize_mode); sessões legadas sem modo são "code".
    const wanted = chatMode ? "chat" : "code";
    return threads.filter((t) => (t.mode ?? "code") === wanted);
  }, [threads, chatMode]);

  const filteredThreads = useMemo(() => {
    if (!searchQuery.trim()) return modeThreads;
    const query = searchQuery.toLowerCase();
    return modeThreads.filter((thread) => {
      const title = thread.metadata?.title?.toLowerCase() ?? "";
      const lastMessage = thread.metadata?.lastMessage?.toLowerCase() ?? "";
      return title.includes(query) || lastMessage.includes(query);
    });
  }, [modeThreads, searchQuery]);

  const { groups: workspaceGroups, orphans } = useMemo(
    () => groupThreadsByWorkspace(filteredThreads, workspaces),
    [filteredThreads, workspaces],
  );

  const grouped = useMemo(() => groupThreads(orphans), [orphans]);

  const isSearching = searchQuery.trim().length > 0;

  const handleDeleteThread = useCallback(
    (threadId: string, e: React.MouseEvent) => {
      e.stopPropagation();
      setPendingDeleteId(threadId);
    },
    [],
  );

  const pendingThread = useMemo(
    () => threads.find((t) => t.thread_id === pendingDeleteId) ?? null,
    [threads, pendingDeleteId],
  );

  // RAG rodando pro workspace da sessão a apagar — mostra aviso extra.
  const pendingHasActiveRag = useMemo(() => {
    const wsId = pendingThread?.workspace_id;
    if (!wsId) return false;
    return Object.values(ragJobs).some(
      (job) =>
        job.workspaceId === wsId &&
        (job.status === "indexing" || job.status === "starting"),
    );
  }, [pendingThread, ragJobs]);

  const handleConfirmDelete = useCallback(() => {
    if (pendingDeleteId) onDeleteThread(pendingDeleteId);
    setPendingDeleteId(null);
  }, [pendingDeleteId, onDeleteThread]);

  const handleCancelDelete = useCallback(() => setPendingDeleteId(null), []);

  const handleRenameThread = useCallback(
    (threadId: string, title: string) => {
      updateThreadMutation.mutate({ id: threadId, updates: { title } });
    },
    [updateThreadMutation],
  );

  const handleTogglePin = useCallback(
    (threadId: string, pinned: boolean) => {
      updateThreadMutation.mutate({ id: threadId, updates: { pinned } });
    },
    [updateThreadMutation],
  );

  const handleClearSearch = useCallback(() => setSearchQuery(""), []);

  const toggleWorkspaceGroup = useCallback((workspaceId: string) => {
    setCollapsedWorkspaces((prev) => {
      const next = new Set(prev);
      if (next.has(workspaceId)) next.delete(workspaceId);
      else next.add(workspaceId);
      return next;
    });
  }, []);

  return (
    <>
      {!isCollapsed && (
        <div
          className="md:hidden fixed inset-0 z-30 bg-background/60 backdrop-blur-sm"
          onClick={onToggle}
          aria-hidden
        />
      )}

      {/* AnimatePresence com os dois estados (collapsed/expanded) como
          filhos diretos — mesmo padrão do workbench-panel.tsx (troca de
          aba). Precisa dos dois estados na mesma árvore pra animar a
          transição entre eles; um early-return por estado não permite. */}
      <AnimatePresence mode="wait" initial={false}>
        {isCollapsed ? (
          <motion.div
            key="collapsed"
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -8 }}
            transition={PANEL_TRANSITION}
            className="contents"
          >
            <CollapsedSidebar
              threads={threads}
              currentThreadId={currentThreadId}
              onToggle={onToggle}
              onSelectThread={onSelectThread}
              onNewChat={onNewChat}
            />
          </motion.div>
        ) : (
          <motion.div
            key="expanded"
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -8 }}
            transition={PANEL_TRANSITION}
            className="contents"
          >
            <aside className="fixed md:relative inset-y-0 left-0 z-40 flex w-72 md:w-full bg-sidebar border-r border-border/40 flex-col">
              <SidebarHeader onToggle={onToggle} />

              <SidebarModeToggle />

              {onNewChat && (
                <NewChatButton onClick={onNewChat} active={isNewSession} />
              )}

              <SessionSearch
                value={searchQuery}
                onChange={setSearchQuery}
                onClear={handleClearSearch}
              />

              <ThreadList
                isLoading={isLoading}
                searchQuery={searchQuery}
                filteredThreads={filteredThreads}
                workspaceGroups={workspaceGroups}
                orphans={orphans}
                grouped={grouped}
                currentThreadId={currentThreadId}
                collapsedWorkspaces={collapsedWorkspaces}
                isSearching={isSearching}
                onSelectThread={onSelectThread}
                onDeleteThread={handleDeleteThread}
                onRenameThread={handleRenameThread}
                onTogglePinThread={handleTogglePin}
                onToggleWorkspace={toggleWorkspaceGroup}
                onRefresh={onRefreshThreads}
              />

              <SidebarFooter />
            </aside>
          </motion.div>
        )}
      </AnimatePresence>

      <ConfirmDialog
        open={pendingDeleteId !== null}
        title={m.session_delete_confirm_title()}
        description={
          pendingHasActiveRag
            ? `${m.session_delete_confirm_desc()} ${m.session_delete_confirm_rag_warning()}`
            : m.session_delete_confirm_desc()
        }
        confirmLabel={m.session_delete_confirm()}
        cancelLabel={m.session_delete_cancel()}
        variant="destructive"
        onConfirm={handleConfirmDelete}
        onCancel={handleCancelDelete}
      />
    </>
  );
});
