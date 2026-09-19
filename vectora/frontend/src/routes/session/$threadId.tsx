import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { motion, useReducedMotion } from "motion/react";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";

import { Sidebar } from "@/components/sidebar/sidebar";
import { Header } from "@/components/header/header";
import { ChatInterface } from "@/components/chat/chat-interface";
import { KanbanBoard } from "@/components/kanban/kanban-board";
import {
  WorkbenchContent,
  WorkbenchNavBar,
} from "@/components/workbench/workbench-panel";
import { IdeModeLayout } from "@/components/layout/ide-mode-layout";
import { CenterCanvas } from "@/components/layout/center-canvas";
import { getModeComposition } from "@/components/layout/mode-composition";
import { ThreeColumnShell } from "@/components/layout/three-column-shell";
import { LicenseBanner } from "@/components/layout/license-banner";
import { m } from "@/lib/paraglide/messages";
import { KeyboardShortcutsDialog } from "@/components/layout/keyboard-shortcuts-dialog";
import {
  CommandPalette,
  type PaletteCommand,
} from "@/components/layout/command-palette";
import { NewChatDialog } from "@/components/sidebar/new-chat-dialog";
import { WindowLayer } from "@/components/workbench/windows/window-layer";
import { WindowDock } from "@/components/workbench/windows/window-dock";
import { DockedEditor } from "@/components/workbench/windows/docked-editor";
import { FileEditor } from "@/components/workbench/file-editor";
import { MarkdownView } from "@/components/workbench/markdown-view";
import { CanvasDocumentDialog } from "@/components/workbench/canvas-document-dialog";
import {
  CommitDetails,
  type GitCommitDetailsState,
} from "@/components/workbench/git/history-view";
import { SessionSwitcher } from "@/components/header/session-switcher";
import { ColumnHeader } from "@/components/layout/column-header";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { useHydrated } from "@/lib/hooks/use-hydrated";
import {
  useIdeLayoutState,
  useIsNarrowViewport,
} from "@/lib/hooks/use-media-query";
import { MOTION_INSTANT, PANEL_TRANSITION } from "@/lib/motion/transitions";
import {
  getPanelWidthFromPointer,
  getResizeDelta,
} from "@/lib/layout/resize-geometry";
import {
  WORKBENCH_CONTENT_MAX_WIDTH,
  WORKBENCH_CONTENT_MIN_WIDTH,
  WORKBENCH_RAIL_WIDTH,
} from "@/lib/layout/workbench-geometry";
import { useWebhookWorkbench } from "@/lib/hooks/use-webhook-workbench";
import { useClampPanelWidths } from "@/lib/hooks/use-clamp-panel-widths";
import { useWorkbenchStore } from "@/lib/stores/workbench-store";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { usePreferenciasDialogStore } from "@/lib/stores/preferencias-dialog-store";

import {
  useThreadsQuery,
  useDeleteThread,
  useUpdateThread,
  threadsQueryKey,
} from "@/lib/queries/threads";
import { useWindowsStore } from "@/lib/stores/windows-store";
import type { PlanItem } from "@/lib/stores/workbench-store";
import type { EditedFile } from "@/lib/types";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import {
  listThreads,
  getHistory,
  type Thread as VectoraThread,
} from "@/lib/api/vectora-client";
import { queryClient } from "../../router";
import { THREAD_FETCH_LIMIT } from "@/lib/constants/features";
import { useAuthStore } from "@/lib/stores/auth-store";
import { getDefaultModel } from "@/lib/config/deployment-config";
import type { AgentConfig } from "@/components/layout/agent-settings";
import { useChatInputStore } from "@/lib/stores/chat-input-store";
import { isNew, clearNew } from "@/lib/stores/new-thread-registry";
import {
  markWorkspaceChosen,
  isWorkspaceChosen,
  markCreateNewWorkspace,
} from "@/lib/stores/workspace-choice-registry";
import { signalWorkspaceChoiceForNewSession } from "@/lib/stores/new-session-signal";
import { useNewSessionId } from "@/lib/hooks/chat/use-new-session-id";
import {
  useBroadcastSync,
  BROADCAST_THREADS,
  BROADCAST_WORKSPACES,
} from "@/lib/hooks/use-broadcast-sync";
import { useGlobalShortcuts } from "@/lib/hooks/use-global-shortcuts";
import { buildOptimisticThread } from "./-thread-cache-helpers";
import { disposeBrowserThread } from "@/lib/browser-session-store";
import { useToastStore } from "@/lib/stores/toast-store";
export const Route = createFileRoute("/session/$threadId")({
  // Só a lista de threads (sidebar) bloqueia a navegação — o histórico da
  // thread ativa é prefetch em background (ver comentário abaixo). O
  // histórico fica em cache no queryClient com chave ['thread-history', id]
  // e é consumido por chat-interface.tsx sem segunda viagem ao servidor.
  // Para "new", o histórico não existe ainda — só prefetch da lista de threads.
  //
  // Mesmo limit que useThreadsQuery (THREAD_FETCH_LIMIT) sob a mesma
  // threadsQueryKey — limit divergente aqui e no loader de "/" causava
  // colisão de cache (um populava a chave com lista truncada, o outro lia
  // stale dentro do staleTime).
  loader: async ({ params }) => {
    const threadsPromise = queryClient.ensureQueryData({
      queryKey: threadsQueryKey(),
      queryFn: () => listThreads(THREAD_FETCH_LIMIT),
      staleTime: 30_000,
    });
    if (params.threadId === "new") {
      await threadsPromise;
      return;
    }
    // Não aguardamos esta promise: histórico pode ser pesado (checkpoint
    // grande) e travar a navegação pra tela anterior por vários segundos
    // sem feedback. Roda em background — chat-interface.tsx consome o
    // cache se chegar a tempo, e refaz o fetch se não (linhas ~479-486),
    // com o skeleton (MessageSkeletons) cobrindo a espera na tela nova.
    void queryClient
      .prefetchQuery({
        queryKey: ["thread-history", params.threadId],
        queryFn: () => getHistory(params.threadId),
        staleTime: 30_000,
      })
      .catch((err: unknown) => {
        console.warn("[loader] prefetch de histórico falhou:", err);
      });
    await threadsPromise;
  },
  component: SessionPage,
});

// Largura da sidebar colapsada — bate com o w-16 do <CollapsedSidebar>.
const SIDEBAR_COLLAPSED_WIDTH = 64;

function SessionPage() {
  const { threadId: routeParam } = Route.useParams() as { threadId: string };
  const navigate = useNavigate();

  // /session/new: o UUID vive só em memória; a URL só recebe o ID real quando
  // a primeira mensagem for persistida (handleThreadUpdate com lastMessage).
  const isNewRoute = routeParam === "new";
  const localNewId = useNewSessionId(routeParam);
  const threadId = isNewRoute ? localNewId : routeParam;
  const userId = useAuthStore((s) => s.user?.id);
  const pushMention = useChatInputStore((s) => s.pushMention);
  const pushDraft = useChatInputStore((s) => s.pushDraft);

  // Painel do workbench: visível e redimensionável via workbench-store. O gate
  // de hidratação evita divergência SSR/cliente do estado persistido.
  const hydrated = useHydrated();
  // Abaixo de Tailwind `sm`, o modo IDE usa o estado mobile e mostra um painel
  // por vez. A largura é medida sem a escala visual do Electron.
  const isNarrowViewport = useIsNarrowViewport();
  const ideLayoutState = useIdeLayoutState();
  const isNarrowIdeViewport = ideLayoutState === "mobile";
  const workbenchOpen = useWorkbenchStore((s) => s.isOpen(threadId));
  const setSplitSize = useWorkbenchStore((s) => s.setSplitSize);

  // CI em tempo real: webhook do GitHub → toast + badge no git-tab (sem F5).
  useWebhookWorkbench();

  // Largura da sidebar (desktop) arrastável pela borda direita.
  const setSidebarWidth = useSettingsStore((s) => s.setSidebarWidth);
  const sidebarPosition = useSettingsStore((s) => s.sidebarPosition);
  const sidebarOnRight = sidebarPosition === "right";
  // The chat column is the physical inverse of the sessions/workbench
  // sidebar when the desktop shell is mirrored.
  const chatPhysicalSide = sidebarOnRight ? "left" : "right";
  const chatResizeEdge = chatPhysicalSide === "right" ? "left" : "right";
  const assistantWorkbenchSide = sidebarOnRight ? "left" : "right";
  const ideWorkbenchSide = sidebarOnRight ? "right" : "left";
  const chatMode = useSettingsStore((s) => s.chatMode);
  const reducedMotion = useReducedMotion();
  const assistantWorkbenchVisible = hydrated && !chatMode && workbenchOpen;
  const setChatMode = useSettingsStore((s) => s.setChatMode);
  const uiMode = useSettingsStore((s) => s.uiMode);
  const setChatSidebarWidth = useSettingsStore((s) => s.setChatSidebarWidth);
  const { sidebarWidth, chatSidebarWidth, splitSize } = useClampPanelWidths();
  // Modelo do chat — lido do store persistido (sobrevive a restart/reload).
  const selectedModel = useSettingsStore((s) => s.selectedModel);
  const setSelectedModel = useSettingsStore((s) => s.setSelectedModel);
  const sidebarWrapRef = useRef<HTMLDivElement>(null);
  const draggingSidebar = useRef(false);

  // Resize do painel de workbench content no modo IDE (borda direita do painel)
  const workbenchResizeRef = useRef<HTMLDivElement>(null);
  // No Assistente a alça fica no limite externo do painel (conteúdo + rail),
  // portanto usa uma referência própria para descontar a largura da rail.
  const assistantWorkbenchResizeRef = useRef<HTMLDivElement>(null);
  const draggingWorkbench = useRef(false);
  const onWorkbenchResizeDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      draggingWorkbench.current = true;
      (e.target as Element).setPointerCapture?.(e.pointerId);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [],
  );
  const onWorkbenchResizeMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingWorkbench.current) return;
      const rect = workbenchResizeRef.current?.getBoundingClientRect();
      if (rect) {
        const width = getPanelWidthFromPointer(
          e.clientX,
          rect,
          ideWorkbenchSide,
        );
        setSplitSize(Math.min(480, Math.max(220, width)));
      }
    },
    [ideWorkbenchSide, setSplitSize],
  );
  const onAssistantWorkbenchResizeMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingWorkbench.current) return;
      const rect = assistantWorkbenchResizeRef.current?.getBoundingClientRect();
      if (!rect) return;
      const outerWidth = getPanelWidthFromPointer(
        e.clientX,
        rect,
        assistantWorkbenchSide,
      );
      setSplitSize(
        Math.min(
          WORKBENCH_CONTENT_MAX_WIDTH,
          Math.max(
            WORKBENCH_CONTENT_MIN_WIDTH,
            outerWidth - WORKBENCH_RAIL_WIDTH,
          ),
        ),
      );
    },
    [assistantWorkbenchSide, setSplitSize],
  );
  const onWorkbenchResizeKeyDown = useCallback(
    (
      e: React.KeyboardEvent<HTMLDivElement>,
      side: "left" | "right" = ideWorkbenchSide,
    ) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      setSplitSize(
        Math.min(480, Math.max(220, splitSize + getResizeDelta(e.key, side))),
      );
    },
    [ideWorkbenchSide, setSplitSize, splitSize],
  );
  const onWorkbenchResizeUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingWorkbench.current) return;
      draggingWorkbench.current = false;
      (e.target as Element).releasePointerCapture?.(e.pointerId);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    },
    [],
  );

  // Resize do painel de chat lateral no modo IDE (borda esquerda do painel)
  const chatSidebarRef = useRef<HTMLDivElement>(null);
  const draggingChatSidebar = useRef(false);
  const onChatSidebarResizeDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      draggingChatSidebar.current = true;
      (e.target as Element).setPointerCapture?.(e.pointerId);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [],
  );
  const onChatSidebarResizeMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingChatSidebar.current) return;
      const rect = chatSidebarRef.current?.getBoundingClientRect();
      if (rect) {
        const width = getPanelWidthFromPointer(
          e.clientX,
          rect,
          chatPhysicalSide,
        );
        setChatSidebarWidth(Math.min(520, Math.max(240, width)));
      }
    },
    [chatPhysicalSide, setChatSidebarWidth],
  );
  const onChatSidebarResizeKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      setChatSidebarWidth(
        Math.min(
          520,
          Math.max(
            240,
            chatSidebarWidth + getResizeDelta(e.key, chatPhysicalSide),
          ),
        ),
      );
    },
    [chatPhysicalSide, chatSidebarWidth, setChatSidebarWidth],
  );
  const onChatSidebarResizeUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingChatSidebar.current) return;
      draggingChatSidebar.current = false;
      (e.target as Element).releasePointerCapture?.(e.pointerId);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    },
    [],
  );

  const onSidebarResizeDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      draggingSidebar.current = true;
      (e.target as Element).setPointerCapture?.(e.pointerId);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [],
  );
  const onSidebarResizeMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingSidebar.current) return;
      const rect = sidebarWrapRef.current?.getBoundingClientRect();
      if (rect) {
        setSidebarWidth(
          sidebarOnRight ? rect.right - e.clientX : e.clientX - rect.left,
        );
      }
    },
    [setSidebarWidth, sidebarOnRight],
  );
  const onSidebarResizeUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingSidebar.current) return;
      draggingSidebar.current = false;
      (e.target as Element).releasePointerCapture?.(e.pointerId);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    },
    [],
  );

  // ── Queries e mutations (TanStack Query) ──────────────────────────────────
  const {
    data: threads = [],
    isLoading,
    refetch: refetchThreads,
  } = useThreadsQuery(userId);
  const deleteThreadMutation = useDeleteThread();
  const updateThreadMutation = useUpdateThread();

  // Sincronização multi-aba: quando outra aba cria/deleta/renomeia uma thread
  // ou altera workspaces, revalida o cache desta aba silenciosamente.
  useBroadcastSync(BROADCAST_THREADS, () => void refetchThreads(), !!userId);
  useBroadcastSync(BROADCAST_WORKSPACES, () => void refetchThreads(), !!userId);

  // Registry central de atalhos globais (C.11) + command palette / cheatsheet (C.30).
  useGlobalShortcuts({
    "ctrl+t": () => {
      void handleConfirmNewChat(null);
      return true;
    },
    "ctrl+backslash": () => {
      const { togglePanel } = useWorkbenchStore.getState();
      togglePanel(threadId);
      return true;
    },
    "ctrl+,": () => {
      usePreferenciasDialogStore.getState().openAt("conta");
      return true;
    },
    "ctrl+k": () => {
      setShowCommandPalette(true);
      return true;
    },
    "ctrl+?": () => {
      setShowShortcutsDialog(true);
      return true;
    },
  });

  // ── UI state local ────────────────────────────────────────────────────────
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [chatSidebarOpen, setChatSidebarOpen] = useState(true);
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);
  const [showToolCalls, setShowToolCalls] = useState(false);
  const [showShortcutsDialog, setShowShortcutsDialog] = useState(false);
  const [showCommandPalette, setShowCommandPalette] = useState(false);
  const [showNewChatDialog, setShowNewChatDialog] = useState(false);
  const [inputLocked, setInputLocked] = useState(false);
  const activeWorkspaceId = useWorkspacesStore((s) => s.active_id);
  const [gitCommitDetails, setGitCommitDetails] =
    useState<GitCommitDetailsState | null>(null);
  const [editedFileDiff, setEditedFileDiff] = useState<{
    file: EditedFile;
    threadId: string;
    workspaceId: string | null;
  } | null>(null);
  const [gitCommitDetailsById, setGitCommitDetailsById] = useState<
    Record<string, GitCommitDetailsState>
  >({});
  const [planDocument, setPlanDocument] = useState<{
    item: PlanItem;
    content: string | null;
  } | null>(null);
  const [planDocumentsById, setPlanDocumentsById] = useState<
    Record<string, { item: PlanItem; content: string | null }>
  >({});
  const [activeCanvasTab, setActiveCanvasTab] = useState("editor");
  const canvasDocuments = useWindowsStore((state) => state.canvasDocuments);
  const activeCanvasDocumentId = useWindowsStore(
    (state) => state.activeCanvasDocumentId,
  );
  const openCanvasDocument = useWindowsStore(
    (state) => state.openCanvasDocument,
  );
  const activateCanvasDocument = useWindowsStore(
    (state) => state.activateCanvasDocument,
  );
  const closeCanvasDocument = useWindowsStore(
    (state) => state.closeCanvasDocument,
  );
  const closeDockedTab = useWindowsStore((state) => state.closeDockedTab);
  const openEditedFileDiff = useCallback(
    (file: EditedFile) => {
      setEditedFileDiff({
        file,
        threadId,
        workspaceId: activeWorkspaceId,
      });
    },
    [activeWorkspaceId, threadId],
  );
  const selectedEditedFile =
    editedFileDiff &&
    editedFileDiff.threadId === threadId &&
    editedFileDiff.workspaceId === (activeWorkspaceId ?? null)
      ? editedFileDiff.file
      : null;
  const openGitCommitDetails = useCallback(
    (details: GitCommitDetailsState) => {
      setGitCommitDetails(details);
      if (activeWorkspaceId) {
        const documentId = `commit:${activeWorkspaceId}:${threadId}:${details.commit.sha}`;
        setGitCommitDetailsById((previous) => ({
          ...previous,
          [documentId]: details,
        }));
        setActiveCanvasTab(documentId);
        openCanvasDocument({
          id: documentId,
          kind: "commit-details",
          workspaceId: activeWorkspaceId,
          threadId,
          title: m.workbench_git_commit_details(),
          commitSha: details.commit.sha,
        });
      }
    },
    [activeWorkspaceId, openCanvasDocument, threadId],
  );
  const openPlanDocument = useCallback(
    (item: PlanItem, content: string | null) => {
      if (!activeWorkspaceId) return;
      const documentId = `plan:${activeWorkspaceId}:${threadId}:${item.path}`;
      setPlanDocument({ item, content });
      setPlanDocumentsById((previous) => ({
        ...previous,
        [documentId]: { item, content },
      }));
      setActiveCanvasTab(documentId);
      openCanvasDocument({
        id: documentId,
        kind: "plan",
        workspaceId: activeWorkspaceId,
        threadId,
        title: item.title,
        path: item.path,
      });
    },
    [activeWorkspaceId, openCanvasDocument, threadId],
  );

  useEffect(() => {
    if (!activeCanvasDocumentId) return;
    const visible = canvasDocuments.some(
      (document) =>
        document.id === activeCanvasDocumentId &&
        document.workspaceId === activeWorkspaceId &&
        (document.kind === "file" || document.threadId === threadId),
    );
    setActiveCanvasTab(visible ? activeCanvasDocumentId : "editor");
  }, [activeCanvasDocumentId, activeWorkspaceId, canvasDocuments, threadId]);

  useEffect(() => {
    setEditedFileDiff(null);
    setPlanDocument(null);
    setGitCommitDetails(null);
  }, [activeWorkspaceId, threadId]);

  useEffect(() => {
    if (!activeWorkspaceId || !gitCommitDetails) return;
    if (
      !canvasDocuments.some(
        (document) =>
          document.id ===
          `commit:${activeWorkspaceId}:${threadId}:${gitCommitDetails.commit.sha}`,
      )
    ) {
      setGitCommitDetails(null);
      setPlanDocument(null);
      setActiveCanvasTab("editor");
    }
  }, [activeWorkspaceId, canvasDocuments, gitCommitDetails, threadId]);
  // `model` reflete `selectedModel` do settings-store (persistido) — o
  // restante de AgentConfig (repos, etc.) é local/efêmero por thread.
  const [agentConfig, setAgentConfigState] = useState<AgentConfig>(() => ({
    model: selectedModel || getDefaultModel(),
  }));
  // Qualquer troca de modelo (seletor no composer, fallback automático por
  // quota) persiste no settings-store — sem isso o modelo escolhido não
  // sobrevivia a um restart/reload (cada mount reiniciava em
  // getDefaultModel()).
  const setAgentConfig = useCallback(
    (config: AgentConfig) => {
      setAgentConfigState(config);
      if (config.model) setSelectedModel(config.model);
    },
    [setSelectedModel],
  );
  // A rehidratação do zustand/persist é assíncrona — no primeiro render
  // `selectedModel` ainda pode ser o default da store recém-criada. Mantém
  // `agentConfig.model` em sincronia sempre que `selectedModel` mudar (por
  // rehidratação ou por outra aba via broadcast); mudanças partindo do
  // próprio `setAgentConfig` já deixam os dois iguais, então o efeito vira
  // no-op nesse caso (sem loop).
  useEffect(() => {
    if (selectedModel && selectedModel !== agentConfig.model) {
      setAgentConfigState((prev) => ({ ...prev, model: selectedModel }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedModel]);

  // ── Navegação ─────────────────────────────────────────────────────────────
  const goTo = useCallback(
    (id: string) =>
      void navigate({
        to: "/session/$threadId",
        params: { threadId: id },
      }),
    [navigate],
  );

  const handleSelectThread = useCallback(
    (id: string) => {
      // Abrir uma sessão entra no modo dela (chat/dev).
      const t = threads.find((th) => th.thread_id === id);
      if (t) setChatMode((t.mode ?? "dev") === "chat");
      // Troca de thread pode trocar de workspace — fecha janelas/editor
      // docked da thread anterior pra não herdar arquivo de outro
      // workspace (mesma proteção de handleNewChat/handleConfirmNewChat/
      // handleDeleteThread).
      setEditedFileDiff(null);
      useWindowsStore.getState().closeAll();
      goTo(id);
      setIsMobileSidebarOpen(false);
    },
    [goTo, threads, setChatMode],
  );

  const handleNewChat = useCallback(() => {
    // Chat: cria sessão direto (sem workspace/folders). Dev: dialog de workspace.
    // Reset do fundo: fecha janelas de arquivo da sessão anterior para a nova
    // conversa não herdar o conteúdo visual da atual.
    setEditedFileDiff(null);
    useWindowsStore.getState().closeAll();
    if (chatMode) {
      void navigate({ to: "/session/$threadId", params: { threadId: "new" } });
      setIsMobileSidebarOpen(false);
      return;
    }
    setShowNewChatDialog(true);
  }, [chatMode, navigate]);

  const handleStartChatFromWelcome = useCallback(() => {
    setChatMode(true);
    markWorkspaceChosen(threadId);
  }, [threadId, setChatMode]);

  const handleConfirmNewChat = useCallback(
    (workspaceId: string | null) => {
      // Não persiste a thread no backend ainda — isso evita acumular
      // conversas vazias na sidebar. A thread só é criada (via StreamChat)
      // quando a primeira mensagem é enviada.
      setEditedFileDiff(null);
      useWindowsStore.getState().closeAll();
      if (isNewRoute) {
        // Já em /session/new: apenas marca o workspace como escolhido (sem navegar).
        if (workspaceId) {
          void useWorkspacesStore.getState().setActive(workspaceId);
        }
        markWorkspaceChosen(threadId);
        if (!workspaceId) {
          // "criar novo workspace" — threadId já é o id definitivo dessa
          // conversa (localNewId), dá pra marcar direto. NÃO reusa o
          // active_id stale do store (de uma conversa anterior); sinaliza
          // pro handler de stream que essa conversa precisa de um workspace
          // dedicado (ChatConfig.create_new_workspace), consumido uma vez
          // no primeiro turno.
          markCreateNewWorkspace(threadId);
        }
      } else {
        // Saindo de uma sessão existente — mesma decisão de sinal usada pela
        // tela inicial (index.tsx::handleDialogConfirm), centralizada em
        // signalWorkspaceChoiceForNewSession pra não duplicar essa lógica
        // (uma cópia divergente foi exatamente a causa do bug de "criar
        // novo workspace" virar "sem escolha" a partir da tela inicial).
        signalWorkspaceChoiceForNewSession(workspaceId);
        void navigate({
          to: "/session/$threadId",
          params: { threadId: "new" },
        });
      }
      setIsMobileSidebarOpen(false);
    },
    [isNewRoute, navigate, threadId],
  );

  const handleDeleteThread = useCallback(
    async (id: string) => {
      await deleteThreadMutation.mutateAsync(id);
      disposeBrowserThread(id);
      if (id !== threadId) return;
      setEditedFileDiff(null);
      useWindowsStore.getState().closeAll();
      if (chatMode) {
        void navigate({ to: "/" });
      } else {
        // Code: não herda uma sessão; navega pra /session/new (sem isso a
        // rota continuava apontando pra thread já deletada, deixando o chat
        // antigo renderizado atrás do modal) e reabre o seletor de workspace
        // (mesmo fluxo da "Nova conversa"), em vez de cair numa sessão herdada.
        void navigate({
          to: "/session/$threadId",
          params: { threadId: "new" },
        });
        setShowNewChatDialog(true);
      }
    },
    [deleteThreadMutation, threadId, navigate, chatMode],
  );

  const handleThreadNotFound = useCallback(() => {
    void navigate({ to: "/" });
  }, [navigate]);

  // A 1ª mensagem de uma thread nova falhou antes de qualquer token chegar
  // (conexão nunca alcançou o backend) — a thread nunca foi persistida
  // (`_upsert_session` só roda quando a requisição de fato chega), então o
  // otimista inserido no cache antes do envio (handleThreadUpdate com
  // lastMessage="") fica sendo uma sessão fantasma só local. Remove.
  const handleThreadPersistFailed = useCallback((id: string) => {
    queryClient.setQueryData<{ threads: VectoraThread[] }>(
      threadsQueryKey(),
      (old) => ({
        threads: (old?.threads ?? []).filter((th) => th.id !== id),
      }),
    );
  }, []);

  const handleThreadUpdate = useCallback(
    (id: string, title: string, lastMessage?: string) => {
      // Chamada otimista do envio da 1ª mensagem (lastMessage vazio): a thread
      // ainda não existe no backend (StreamChat ainda não rodou), então só
      // refletimos na sidebar localmente — sem chamar UpdateThread (404).
      if (isNew(id) && !lastMessage) {
        queryClient.setQueryData<{ threads: VectoraThread[] }>(
          threadsQueryKey(),
          (old) => {
            const existing = old?.threads ?? [];
            if (existing.some((th) => th.id === id)) return old;
            const optimistic = buildOptimisticThread({
              id,
              title,
              workspaceId: useWorkspacesStore.getState().active_id ?? "",
              chatMode,
            });
            return { threads: [optimistic, ...existing] };
          },
        );
        return;
      }
      // Primeira persistência da thread no backend: remove do registry de novas.
      if (isNew(id)) clearNew(id);
      // Se estávamos em /session/new, atualiza a URL para o ID real (replace
      // para que o botão Voltar do browser não retorne a /session/new vazio).
      if (isNewRoute) {
        void navigate({
          to: "/session/$threadId",
          params: { threadId: id },
          replace: true,
        });
      }
      // Patch local imediato do cache — a sidebar não pode depender só do
      // round-trip de `updateThreadMutation` (rede lenta/generateTitle
      // assíncrono) para refletir a thread: sem isso ela só aparecia após
      // reload, quando ListThreads era refeito do zero. Atualiza a entrada
      // existente (posta pelo passo otimista acima) ou insere uma nova —
      // defensivo para o caso de handleRegenerate/handleEditAndRerun
      // chamarem onThreadUpdate direto numa thread ainda não presente.
      queryClient.setQueryData<{ threads: VectoraThread[] }>(
        threadsQueryKey(),
        (old) => {
          const existing = old?.threads ?? [];
          const now = new Date().toISOString();
          if (existing.some((th) => th.id === id)) {
            return {
              threads: existing.map((th) =>
                th.id === id ? { ...th, title, updated_at: now } : th,
              ),
            };
          }
          const inserted = buildOptimisticThread({
            id,
            title,
            workspaceId: useWorkspacesStore.getState().active_id ?? "",
            chatMode,
            now,
          });
          return { threads: [inserted, ...existing] };
        },
      );
      void updateThreadMutation.mutate({ id, updates: { title } });
    },
    [updateThreadMutation, isNewRoute, navigate, chatMode],
  );

  // ── Command palette — lista de ações navegáveis (C.30) ───────────────────
  const paletteCommands = useMemo<PaletteCommand[]>(
    () => [
      {
        id: "new-chat",
        label: m.palette_cmd_new_chat(),
        category: m.palette_cat_navigation(),
        shortcut: "Ctrl+T",
        run: () => void handleConfirmNewChat(null),
      },
      {
        id: "settings",
        label: m.palette_cmd_settings(),
        category: m.palette_cat_navigation(),
        shortcut: "Ctrl+,",
        run: () => usePreferenciasDialogStore.getState().openAt("conta"),
      },
      {
        id: "keyboard-shortcuts",
        label: m.palette_cmd_keyboard_shortcuts(),
        category: m.palette_cat_navigation(),
        shortcut: "Ctrl+?",
        run: () => setShowShortcutsDialog(true),
      },
      {
        id: "toggle-workbench",
        label: m.palette_cmd_toggle_workbench(),
        category: m.palette_cat_workbench(),
        shortcut: "Ctrl+\\",
        run: () => useWorkbenchStore.getState().togglePanel(threadId),
      },
      {
        id: "clear-messages",
        label: m.palette_cmd_clear_messages(),
        category: m.palette_cat_chat(),
        shortcut: "Ctrl+L",
        run: () => {
          // Disparado pelo atalho Ctrl+L — o chat-interface escuta esse evento.
          document.dispatchEvent(new CustomEvent("vectora:clear-messages"));
        },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [threadId],
  );

  // Sessão nova/vazia (ainda sem 1ª mensagem persistida) → destaca "Nova sessão".
  const isNewSession = isNew(threadId);

  // Threads do workspace ativo (para o session switcher do IDE mode).
  const wsThreads = useMemo(
    () =>
      activeWorkspaceId
        ? threads.filter((t) => t.workspace_id === activeWorkspaceId)
        : threads,
    [threads, activeWorkspaceId],
  );

  // ── Sidebar (instância única reutilizada em desktop e mobile Sheet) ───────
  const sidebar = useMemo(
    () => (
      <Sidebar
        isCollapsed={isSidebarCollapsed}
        onToggle={() => setIsSidebarCollapsed((v) => !v)}
        threads={threads}
        currentThreadId={threadId}
        onSelectThread={handleSelectThread}
        onDeleteThread={handleDeleteThread}
        onNewChat={handleNewChat}
        isLoading={isLoading}
        isNewSession={isNewSession}
        showHeader
        onRefreshThreads={async () => {
          const result = await refetchThreads();
          if (result.isError || result.error) {
            useToastStore.getState().error(m.threads_error_list());
          }
        }}
      />
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      threads,
      threadId,
      isLoading,
      isSidebarCollapsed,
      isNewSession,
      uiMode,
      chatMode,
    ],
  );

  // Painel da sidebar (largura arrastável + handle de resize) — extraído do
  // layout "Assistente" pra ser reusado também no Kanban, que antes escondia
  // a sidebar por completo (sem jeito de trocar de sessão com o board aberto).
  const sidebarPanel = useMemo(
    () => (
      <motion.div
        ref={sidebarWrapRef}
        className="hidden md:flex shrink-0 relative"
        animate={{
          width: isSidebarCollapsed
            ? SIDEBAR_COLLAPSED_WIDTH
            : hydrated
              ? sidebarWidth
              : 224,
        }}
        transition={
          draggingSidebar.current || reducedMotion
            ? MOTION_INSTANT
            : PANEL_TRANSITION
        }
      >
        {sidebar}
        {!isSidebarCollapsed && (
          <div
            role="separator"
            aria-orientation="vertical"
            onPointerDown={onSidebarResizeDown}
            onPointerMove={onSidebarResizeMove}
            onPointerUp={onSidebarResizeUp}
            onPointerCancel={onSidebarResizeUp}
            className={`absolute top-0 ${sidebarOnRight ? "left-0" : "right-0"} z-50 h-full w-1 cursor-col-resize bg-transparent hover:bg-border transition-colors`}
          />
        )}
      </motion.div>
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sidebar, isSidebarCollapsed, hydrated, sidebarWidth, sidebarOnRight],
  );

  const headerEl = useMemo(
    () => (
      <Header
        showToolCalls={showToolCalls}
        onToggleToolCalls={() => setShowToolCalls((v) => !v)}
        onShowShortcuts={() => setShowShortcutsDialog(true)}
        onOpenSidebar={() => setIsMobileSidebarOpen(true)}
        showModeSwitch={!chatMode}
      />
    ),
    [showToolCalls, chatMode],
  );

  // Cada modo escolhe explicitamente a coluna esquerda. Assistente e Kanban
  // usam a lista de sessões; IDE usa a workbench. Manter a sidebar de sessões
  // fora do IDE evitava que o shell tivesse quatro colunas concorrentes.
  const showSidebarPanel = uiMode !== "ide";
  const modeComposition = getModeComposition(uiMode);

  // Chat renderizado no fluxo normal do layout de cada modo. `compact`
  // (IDE) muda densidade e liga o SessionSwitcher acima dele; a posição
  // do scroll sobrevive à troca de modo via `message-list.tsx`, que
  // guarda e restaura por thread — não por manter a instância montada.
  const renderChatPanel = useCallback(
    (compact: boolean, onCollapse?: () => void) => {
      const welcomeActions =
        !compact && hydrated && isNewRoute && !isWorkspaceChosen(threadId);
      return (
        <div className="flex flex-col h-full min-h-0 overflow-hidden">
          {compact && (
            <ColumnHeader className="gap-1 px-2 border-border/40 min-w-0">
              <SessionSwitcher
                threads={wsThreads}
                currentThreadId={threadId}
                onSelectThread={handleSelectThread}
                onNewSession={handleNewChat}
              />
              {onCollapse && (
                <button
                  type="button"
                  aria-label={m.sidebar_collapse()}
                  title={m.sidebar_collapse()}
                  onClick={onCollapse}
                  className="ml-auto rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <PanelRightClose className="h-4 w-4" />
                </button>
              )}
            </ColumnHeader>
          )}
          <div className="flex-1 min-h-0">
            <ChatInterface
              threadId={threadId}
              showToolCalls={showToolCalls}
              agentConfig={agentConfig}
              onAgentConfigChange={setAgentConfig}
              onThreadUpdate={handleThreadUpdate}
              onThreadPersistFailed={handleThreadPersistFailed}
              onThreadNotFound={handleThreadNotFound}
              onOpenEditedFile={openEditedFileDiff}
              inputLocked={inputLocked}
              isNewThread={isNew(threadId)}
              compact={compact}
              onStartChat={
                welcomeActions ? handleStartChatFromWelcome : undefined
              }
              onStartCode={
                welcomeActions ? () => setShowNewChatDialog(true) : undefined
              }
            />
          </div>
        </div>
      );
    },
    [
      wsThreads,
      threadId,
      handleSelectThread,
      handleNewChat,
      showToolCalls,
      agentConfig,
      setAgentConfig,
      handleThreadUpdate,
      handleThreadPersistFailed,
      handleThreadNotFound,
      inputLocked,
      hydrated,
      isNewRoute,
      handleStartChatFromWelcome,
    ],
  );

  return (
    <div
      className="flex flex-col h-full overflow-hidden bg-background"
      data-mode-left={modeComposition.left}
      data-mode-center={modeComposition.center}
      data-mode-right={modeComposition.right ?? "hidden"}
    >
      <LicenseBanner fullWidth onBlockingChange={setInputLocked} />

      <div className="relative flex flex-1 min-h-0 overflow-hidden">
        {showSidebarPanel && (
          <Sheet
            open={isMobileSidebarOpen}
            onOpenChange={setIsMobileSidebarOpen}
          >
            <SheetContent
              side={sidebarOnRight ? "right" : "left"}
              className={`p-0 w-72 ${sidebarOnRight ? "border-l" : "border-r"}`}
            >
              {sidebar}
            </SheetContent>
          </Sheet>
        )}

        <div className="flex flex-col flex-1 min-w-0 min-h-0 overflow-hidden">
          {/* `headerEl` é montado uma única vez e entregue ao slot físico
              central de ThreeColumnShell. Cada modo troca apenas os slots de
              conteúdo ao redor dele; o Header nunca entra na animação das
              colunas externas. */}

          {uiMode === "kanban" && !chatMode ? (
            // min-w-[360px]: piso mínimo pro conteúdo continuar legível
            // quando a janela encolhe. Kanban não tem coluna lateral
            // direita — header cobre a largura toda por não ter nada ao
            // lado com quem competir.
            <motion.div
              key="kanban-mode"
              initial={reducedMotion ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={reducedMotion ? MOTION_INSTANT : PANEL_TRANSITION}
              className="flex flex-1 min-h-0 min-w-0 overflow-hidden"
            >
              <ThreeColumnShell
                centerHeader={headerEl}
                left={sidebarPanel}
                center={
                  <div className="flex min-w-[360px] flex-1 min-h-0 overflow-hidden">
                    <KanbanBoard threadId={threadId} />
                  </div>
                }
                right={null}
                columns={{
                  left: { label: "Sessões" },
                  center: { label: "Kanban" },
                  right: { label: "Workbench", visibility: "hidden" },
                }}
                direction={sidebarOnRight ? "rtl" : "ltr"}
              />
            </motion.div>
          ) : uiMode === "ide" && !chatMode ? (
            // ── Layout IDE ──────────────────────────────────────────────
            <motion.div
              key="ide-mode"
              initial={reducedMotion ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={reducedMotion ? MOTION_INSTANT : PANEL_TRANSITION}
              className="flex flex-col flex-1 min-h-0 overflow-hidden"
            >
              <IdeModeLayout
                workbenchOpen={workbenchOpen}
                layoutState={ideLayoutState}
                direction={sidebarOnRight ? "rtl" : "ltr"}
                workbenchSide={ideWorkbenchSide}
                // The shell column includes the fixed 48px rail. splitSize
                // remains the width of the resizable content panel itself.
                workbenchWidth={
                  hydrated && workbenchOpen
                    ? splitSize + WORKBENCH_RAIL_WIDTH
                    : WORKBENCH_RAIL_WIDTH
                }
                workbenchMinWidth={
                  workbenchOpen
                    ? WORKBENCH_CONTENT_MIN_WIDTH + WORKBENCH_RAIL_WIDTH
                    : WORKBENCH_RAIL_WIDTH
                }
                workbenchMaxWidth={
                  WORKBENCH_CONTENT_MAX_WIDTH + WORKBENCH_RAIL_WIDTH
                }
                chatWidth={
                  hydrated ? (chatSidebarOpen ? chatSidebarWidth : 48) : 256
                }
                chatMinWidth={chatSidebarOpen ? 240 : 48}
                chatMaxWidth={520}
                showChat={chatSidebarOpen}
                header={headerEl}
                navBar={
                  <WorkbenchNavBar
                    threadId={threadId}
                    side={ideWorkbenchSide}
                  />
                }
                workbenchContent={
                  <div
                    ref={workbenchResizeRef}
                    className={
                      isNarrowIdeViewport
                        ? "relative flex-1 min-w-0"
                        : "relative shrink-0 overflow-hidden"
                    }
                    style={
                      isNarrowIdeViewport
                        ? undefined
                        : { width: hydrated && workbenchOpen ? splitSize : 0 }
                    }
                    aria-hidden={!workbenchOpen}
                  >
                    <WorkbenchContent
                      threadId={threadId}
                      onOpenCommitDetails={openGitCommitDetails}
                      onOpenPlanDocument={openPlanDocument}
                      side={ideWorkbenchSide}
                      visible={hydrated && workbenchOpen}
                      onAddToContext={pushMention}
                      onSendPrompt={pushDraft}
                    />
                    {!isNarrowIdeViewport && workbenchOpen && (
                      <div
                        role="separator"
                        aria-orientation="vertical"
                        aria-label={m.resize_workbench()}
                        aria-valuemin={220}
                        aria-valuemax={480}
                        aria-valuenow={splitSize}
                        tabIndex={0}
                        onKeyDown={(event) =>
                          onWorkbenchResizeKeyDown(event, ideWorkbenchSide)
                        }
                        onPointerDown={onWorkbenchResizeDown}
                        onPointerMove={onWorkbenchResizeMove}
                        onPointerUp={onWorkbenchResizeUp}
                        onPointerCancel={onWorkbenchResizeUp}
                        className={`absolute ${ideWorkbenchSide === "left" ? "right-0" : "left-0"} top-0 z-[100] h-full w-1 cursor-col-resize touch-none select-none bg-border/60 hover:bg-primary/50 transition-colors`}
                      />
                    )}
                  </div>
                }
                editor={
                  // min-w-[360px]: piso mínimo pro editor continuar usável
                  // ao encolher a janela ou puxar o painel do workbench largo.
                  <div className="flex flex-col flex-1 min-w-0 h-full overflow-hidden">
                    <CenterCanvas
                      activeTab={activeCanvasTab}
                      onTabChange={(id) => {
                        setActiveCanvasTab(id);
                        if (
                          (id.startsWith("commit:") ||
                            id.startsWith("file:") ||
                            id.startsWith("plan:")) &&
                          activeWorkspaceId
                        ) {
                          activateCanvasDocument(id);
                        }
                      }}
                      onTabClose={(id) => {
                        const document = canvasDocuments.find(
                          (item) => item.id === id,
                        );
                        if (document?.kind === "file" && document.path) {
                          closeDockedTab(document.path);
                        } else {
                          closeCanvasDocument(id);
                        }
                        if (id === activeCanvasTab)
                          setActiveCanvasTab("editor");
                      }}
                      documents={canvasDocuments.filter(
                        (document) =>
                          document.workspaceId === activeWorkspaceId &&
                          (document.kind === "file" ||
                            document.threadId === threadId),
                      )}
                      renderDocument={(document) => {
                        if (document.kind === "file" && document.path) {
                          return (
                            <FileEditor
                              workspaceId={document.workspaceId}
                              path={document.path}
                            />
                          );
                        }
                        if (document.kind === "commit-details") {
                          const details = gitCommitDetailsById[document.id];
                          return details ? (
                            <CommitDetails
                              commit={details.commit}
                              diff={details.diff}
                              loading={details.loading}
                            />
                          ) : (
                            <CommitDetails
                              commit={{
                                sha: document.commitSha ?? "",
                                sha_short: (document.commitSha ?? "").slice(
                                  0,
                                  7,
                                ),
                                message: document.title,
                                body: "",
                                author: "",
                                date: "",
                                refs: [],
                              }}
                              diff={null}
                              loading
                            />
                          );
                        }
                        const plan = planDocumentsById[document.id];
                        return (
                          <div className="h-full overflow-auto p-5">
                            {plan?.content ? (
                              <MarkdownView content={plan.content} />
                            ) : (
                              <p className="text-sm text-muted-foreground">
                                {m.workbench_preview_md_loading()}
                              </p>
                            )}
                          </div>
                        );
                      }}
                    >
                      <DockedEditor activeWorkspaceId={activeWorkspaceId} />
                    </CenterCanvas>
                  </div>
                }
                chat={
                  <div
                    ref={chatSidebarRef}
                    className={
                      isNarrowIdeViewport
                        ? "relative flex flex-col h-full bg-sidebar"
                        : `relative shrink-0 flex flex-col h-full border-border/60 bg-sidebar ${sidebarOnRight ? "border-r" : "border-l"}`
                    }
                    style={
                      isNarrowIdeViewport
                        ? undefined
                        : {
                            width: hydrated
                              ? chatSidebarOpen
                                ? chatSidebarWidth
                                : 48
                              : 256,
                          }
                    }
                  >
                    {!isNarrowIdeViewport && (
                      <div
                        role="separator"
                        aria-orientation="vertical"
                        aria-label={m.resize_chat()}
                        aria-valuemin={240}
                        aria-valuemax={520}
                        aria-valuenow={chatSidebarWidth}
                        tabIndex={0}
                        onKeyDown={onChatSidebarResizeKeyDown}
                        onPointerDown={onChatSidebarResizeDown}
                        onPointerMove={onChatSidebarResizeMove}
                        onPointerUp={onChatSidebarResizeUp}
                        onPointerCancel={onChatSidebarResizeUp}
                        className={`absolute ${chatResizeEdge === "left" ? "left-0" : "right-0"} top-0 z-[100] h-full w-1 cursor-col-resize touch-none select-none bg-border/60 hover:bg-primary/50 transition-colors`}
                      />
                    )}
                    {chatSidebarOpen ? (
                      <div className="flex-1 min-h-0 min-w-0">
                        {renderChatPanel(true, () => setChatSidebarOpen(false))}
                      </div>
                    ) : (
                      <div className="flex h-full min-h-0 w-full flex-col">
                        <div className="flex h-16 min-h-16 shrink-0 items-center justify-center border-b border-border/60 bg-sidebar">
                          <button
                            type="button"
                            aria-label={m.sidebar_open()}
                            title={m.sidebar_open()}
                            onClick={() => setChatSidebarOpen(true)}
                            className="flex h-full w-full items-center justify-center text-muted-foreground hover:bg-muted hover:text-foreground"
                          >
                            <PanelRightOpen className="h-4 w-4" />
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                }
              />

              <KeyboardShortcutsDialog
                open={showShortcutsDialog}
                onOpenChange={setShowShortcutsDialog}
              />
              <CommandPalette
                open={showCommandPalette}
                onOpenChange={setShowCommandPalette}
                commands={paletteCommands}
              />
              <NewChatDialog
                open={showNewChatDialog}
                onOpenChange={setShowNewChatDialog}
                onConfirm={(workspaceId) =>
                  void handleConfirmNewChat(workspaceId)
                }
              />
            </motion.div>
          ) : (
            // ── Layout Assistente/Chat (atual) ─────────────────────────────────
            <motion.div
              key="assistant-mode"
              initial={reducedMotion ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={reducedMotion ? MOTION_INSTANT : PANEL_TRANSITION}
              className="flex flex-1 min-h-0 overflow-hidden"
            >
              <ThreeColumnShell
                centerHeader={headerEl}
                left={sidebarPanel}
                center={
                  <div className="flex flex-1 min-w-0 min-h-0 flex-col overflow-hidden">
                    {isNarrowViewport && assistantWorkbenchVisible ? (
                      <div className="flex h-full min-w-0">
                        {assistantWorkbenchSide === "left" && (
                          <WorkbenchNavBar
                            threadId={threadId}
                            side={assistantWorkbenchSide}
                          />
                        )}
                        <div className="min-w-0 flex-1">
                          <WorkbenchContent
                            threadId={threadId}
                            onOpenCommitDetails={openGitCommitDetails}
                            onOpenPlanDocument={openPlanDocument}
                            side={assistantWorkbenchSide}
                            visible
                            onAddToContext={pushMention}
                            onSendPrompt={pushDraft}
                          />
                        </div>
                        {assistantWorkbenchSide === "right" && (
                          <WorkbenchNavBar
                            threadId={threadId}
                            side={assistantWorkbenchSide}
                          />
                        )}
                      </div>
                    ) : (
                      renderChatPanel(false)
                    )}
                  </div>
                }
                right={
                  <div
                    ref={assistantWorkbenchResizeRef}
                    className={`relative flex min-w-0 shrink-0 min-h-0 flex-row overflow-hidden border-border/60 ${assistantWorkbenchSide === "right" ? "border-l" : "border-r"}`}
                    style={{
                      width: assistantWorkbenchVisible
                        ? splitSize + WORKBENCH_RAIL_WIDTH
                        : WORKBENCH_RAIL_WIDTH,
                      minWidth: assistantWorkbenchVisible
                        ? WORKBENCH_CONTENT_MIN_WIDTH + WORKBENCH_RAIL_WIDTH
                        : WORKBENCH_RAIL_WIDTH,
                      maxWidth:
                        WORKBENCH_CONTENT_MAX_WIDTH + WORKBENCH_RAIL_WIDTH,
                    }}
                  >
                    {assistantWorkbenchSide === "left" && (
                      <WorkbenchNavBar
                        threadId={threadId}
                        side={assistantWorkbenchSide}
                      />
                    )}
                    {assistantWorkbenchVisible && (
                      <WorkbenchContent
                        threadId={threadId}
                        onOpenCommitDetails={openGitCommitDetails}
                        onOpenPlanDocument={openPlanDocument}
                        side={assistantWorkbenchSide}
                        visible
                        onAddToContext={pushMention}
                        onSendPrompt={pushDraft}
                      />
                    )}
                    {assistantWorkbenchVisible && (
                      <div
                        role="separator"
                        aria-orientation="vertical"
                        aria-label={m.resize_workbench()}
                        aria-valuemin={WORKBENCH_CONTENT_MIN_WIDTH}
                        aria-valuemax={WORKBENCH_CONTENT_MAX_WIDTH}
                        aria-valuenow={splitSize}
                        tabIndex={0}
                        onKeyDown={(event) =>
                          onWorkbenchResizeKeyDown(
                            event,
                            assistantWorkbenchSide,
                          )
                        }
                        onPointerDown={onWorkbenchResizeDown}
                        onPointerMove={onAssistantWorkbenchResizeMove}
                        onPointerUp={onWorkbenchResizeUp}
                        onPointerCancel={onWorkbenchResizeUp}
                        className={`absolute ${assistantWorkbenchSide === "left" ? "right-0" : "left-0"} top-0 z-[100] h-full w-1 cursor-col-resize touch-none select-none bg-border/60 hover:bg-primary/50 transition-colors`}
                      />
                    )}
                    {assistantWorkbenchSide === "right" && (
                      <WorkbenchNavBar
                        threadId={threadId}
                        side={assistantWorkbenchSide}
                      />
                    )}
                  </div>
                }
                showRight={hydrated && !chatMode && !isNarrowViewport}
                direction={sidebarOnRight ? "rtl" : "ltr"}
                columns={{
                  left: { label: "Sessões" },
                  center: { label: "Chat" },
                  right: { label: "Workbench" },
                }}
              />

              {/* Dialogs globais */}
              <KeyboardShortcutsDialog
                open={showShortcutsDialog}
                onOpenChange={setShowShortcutsDialog}
              />
              <CommandPalette
                open={showCommandPalette}
                onOpenChange={setShowCommandPalette}
                commands={paletteCommands}
              />
              <NewChatDialog
                open={showNewChatDialog}
                onOpenChange={setShowNewChatDialog}
                onConfirm={(workspaceId) =>
                  void handleConfirmNewChat(workspaceId)
                }
              />

              {/* Workstation: janelas flutuantes de arquivos + dock de minimizadas */}
              <WindowLayer />
              <WindowDock />
            </motion.div>
          )}
        </div>
        <CanvasDocumentDialog
          open={uiMode === "assistant" && gitCommitDetails !== null}
          onOpenChange={(open) => {
            if (!open) setGitCommitDetails(null);
          }}
          title={
            gitCommitDetails?.commit.message ?? m.workbench_git_commit_details()
          }
        >
          {gitCommitDetails && (
            <CommitDetails
              commit={gitCommitDetails.commit}
              diff={gitCommitDetails.diff}
              loading={gitCommitDetails.loading}
            />
          )}
        </CanvasDocumentDialog>
        <CanvasDocumentDialog
          open={selectedEditedFile !== null}
          onOpenChange={(open) => {
            if (!open) setEditedFileDiff(null);
          }}
          title={
            selectedEditedFile
              ? m.chat_edited_files_diff({ path: selectedEditedFile.path })
              : "Diff"
          }
          titleClassName="truncate font-mono text-sm"
        >
          <div className="h-full overflow-auto bg-background p-4">
            {selectedEditedFile?.hunks.length ? (
              selectedEditedFile.hunks.map((hunk, index) => (
                <div key={`${hunk.header}-${index}`} className="mb-4 last:mb-0">
                  <div className="mb-1 font-mono text-xs text-muted-foreground">
                    {hunk.header}
                  </div>
                  <pre className="overflow-x-auto rounded-md border border-border/50 bg-muted/20 p-3 font-mono text-xs leading-5">
                    {hunk.lines.map((line, lineIndex) => (
                      <span
                        key={`${lineIndex}-${line}`}
                        className={
                          line.startsWith("+")
                            ? "text-git-addition"
                            : line.startsWith("-")
                              ? "text-destructive"
                              : "text-foreground/80"
                        }
                      >
                        {line}
                        {"\n"}
                      </span>
                    ))}
                  </pre>
                </div>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                {m.chat_edited_files_diff_empty()}
              </p>
            )}
          </div>
        </CanvasDocumentDialog>
      </div>
    </div>
  );
}
