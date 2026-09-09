import { useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Sidebar } from "@/components/sidebar/sidebar";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { LicenseBanner } from "@/components/layout/license-banner";
import { useAuthStore } from "@/lib/stores/auth-store";
import { useSettingsStore } from "@/lib/stores/settings-store";
import {
  useThreadsQuery,
  useDeleteThread,
  threadsQueryKey,
} from "@/lib/queries/threads";
import { queryClient } from "../router";
import { listThreads } from "@/lib/api/vectora-client";
import { THREAD_FETCH_LIMIT } from "@/lib/constants/features";
import { EmptyStateHeader } from "@/components/chat/features/empty-state-header";
import { NewChatDialog } from "@/components/sidebar/new-chat-dialog";
import { Header } from "@/components/header/header";
import {
  signalWorkspacePreChosen,
  signalWorkspaceChoiceForNewSession,
} from "@/lib/stores/new-session-signal";
import { disposeBrowserThread } from "@/lib/browser-session-store";
import { useToastStore } from "@/lib/stores/toast-store";
import { m } from "@/lib/paraglide/messages";

/** Largura da sidebar na tela inicial — mais larga que o normal para dar destaque. */
const HOME_SIDEBAR_WIDTH = 280;
/** Duração da animação de encolhimento em ms, sincronizada com o CSS. */
const LEAVE_DURATION_MS = 280;

export const Route = createFileRoute("/")({
  loader: async () => {
    // Mesmo limit que useThreadsQuery (THREAD_FETCH_LIMIT) — usar um limit
    // diferente aqui populava o cache sob a mesma threadsQueryKey com uma
    // lista truncada, que o loader de /session/$threadId (ensureQueryData,
    // sem refetchOnMount) podia servir stale dentro do staleTime de 30s.
    await queryClient.ensureQueryData({
      queryKey: threadsQueryKey(),
      queryFn: () => listThreads(THREAD_FETCH_LIMIT),
      staleTime: 30_000,
    });
  },
  component: HomeScreen,
});

function HomeScreen() {
  const navigate = useNavigate();
  const userId = useAuthStore((s) => s.user?.id);
  const {
    data: threads = [],
    isLoading,
    refetch: refetchThreads,
  } = useThreadsQuery(userId);
  const deleteThread = useDeleteThread();
  const setChatMode = useSettingsStore((s) => s.setChatMode);
  const sidebarWidth = useSettingsStore((s) => s.sidebarWidth);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [showDialog, setShowDialog] = useState(false);
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);

  /** Anima a sidebar encolhendo e navega após a animação terminar. */
  const go = (fn: () => void) => {
    if (leaving) return;
    setLeaving(true);
    setTimeout(fn, LEAVE_DURATION_MS);
  };

  const handleSelectThread = (id: string) => {
    go(() => {
      const t = threads.find((th) => th.thread_id === id);
      if (t) setChatMode((t.mode ?? "dev") === "chat");
      void navigate({ to: "/session/$threadId", params: { threadId: id } });
    });
  };

  const handleNewChat = () => {
    go(() => {
      void navigate({ to: "/session/$threadId", params: { threadId: "new" } });
    });
  };

  const handleStartChat = () => {
    go(() => {
      setChatMode(true);
      signalWorkspacePreChosen();
      void navigate({ to: "/session/$threadId", params: { threadId: "new" } });
    });
  };

  /** Abre o seletor de workspace direto — após confirmar, navega para a sessão. */
  const handleStartCode = () => {
    setShowDialog(true);
  };

  const handleDialogConfirm = (workspaceId: string | null) => {
    setChatMode(false);
    // Mesma decisão de sinal usada por $threadId.tsx::handleConfirmNewChat
    // ao sair de uma sessão existente — centralizada em
    // signalWorkspaceChoiceForNewSession pra não poder duplicar e divergir
    // de novo (era exatamente isso que causava "criar novo workspace" na
    // tela inicial se comportar como "sem escolha").
    signalWorkspaceChoiceForNewSession(workspaceId);
    go(() => {
      void navigate({ to: "/session/$threadId", params: { threadId: "new" } });
    });
  };

  const handleDeleteThread = async (id: string) => {
    await deleteThread.mutateAsync(id);
    disposeBrowserThread(id);
  };

  const refreshThreads = async (): Promise<void> => {
    const result = await refetchThreads();
    if (result.isError || result.error) {
      useToastStore.getState().error(m.threads_error_list());
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden bg-background">
      <LicenseBanner fullWidth />
      <div className="flex flex-1 min-h-0 overflow-hidden">
        <div
          className="hidden md:flex shrink-0"
          style={{
            width: leaving ? sidebarWidth : HOME_SIDEBAR_WIDTH,
            transition: `width ${LEAVE_DURATION_MS}ms ease-in-out`,
          }}
        >
          <Sidebar
            isCollapsed={isCollapsed}
            onToggle={() => setIsCollapsed((v) => !v)}
            threads={threads}
            currentThreadId=""
            onSelectThread={handleSelectThread}
            onDeleteThread={handleDeleteThread}
            onNewChat={handleNewChat}
            isLoading={isLoading}
            isNewSession={false}
            onRefreshThreads={refreshThreads}
          />
        </div>
        {/* Mobile: a sidebar de verdade some (hidden md:flex acima) — sem
            isso, não haveria como abrir a lista de sessões em tela estreita
            nesta rota (diferente de /session/$threadId, que já tinha esse
            fallback). */}
        <Sheet open={isMobileSidebarOpen} onOpenChange={setIsMobileSidebarOpen}>
          <SheetContent side="left" className="p-0 w-72 border-r">
            <Sidebar
              isCollapsed={false}
              onToggle={() => setIsMobileSidebarOpen(false)}
              threads={threads}
              currentThreadId=""
              onSelectThread={(id) => {
                setIsMobileSidebarOpen(false);
                handleSelectThread(id);
              }}
              onDeleteThread={handleDeleteThread}
              onNewChat={() => {
                setIsMobileSidebarOpen(false);
                handleNewChat();
              }}
              isLoading={isLoading}
              isNewSession={false}
              onRefreshThreads={refreshThreads}
            />
          </SheetContent>
        </Sheet>

        <main className="flex-1 min-h-0 overflow-auto flex flex-col">
          <Header onOpenSidebar={() => setIsMobileSidebarOpen(true)} />
          <EmptyStateHeader
            onStartChat={handleStartChat}
            onStartCode={handleStartCode}
          />
        </main>
      </div>

      <NewChatDialog
        open={showDialog}
        onOpenChange={setShowDialog}
        onConfirm={handleDialogConfirm}
      />
    </div>
  );
}
