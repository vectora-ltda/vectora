import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { motion, useReducedMotion } from "motion/react";
import { PanelRightClose } from "lucide-react";
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
import { CanvasFileDiff } from "@/components/workbench/canvas-file-diff";
import { LibraryMcpPreview } from "@/components/workbench/library-mcp-preview";
import { SessionSwitcher } from "@/components/header/session-switcher";
import { ColumnHeader } from "@/components/layout/column-header";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { useHydrated } from "@/lib/hooks/use-hydrated";
import {
  useIdeLayoutState,
  useIsNarrowViewport,
  useSessionLayoutState,
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
import {
  CHAT_SIDEBAR_OPEN_MIN_WIDTH,
  SIDE_COLUMN_MIN_WIDTH,
} from "@/lib/layout/panel-geometry";
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
import { isCanvasDocumentVisible } from "@/lib/canvas-document-visibility";
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
import {
  isNew,
  clearNew,
  useIsNewThread,
} from "@/lib/stores/new-thread-registry";
import {
  markWorkspaceChosen,
  useIsWorkspaceChosen,
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
  const canvasDocuments = useWindowsStore((s) => s.canvasDocuments);
  const activeCanvasDocumentId = useWindowsStore(
    (s) => s.activeCanvasDocumentId,
  );
  const openCanvasDocument = useWindowsStore((s) => s.openCanvasDocument);
  const activateCanvasDocument = useWindowsStore(
    (s) => s.activateCanvasDocument,
  );
  const closeCanvasDocument = useWindowsStore(
    (s) => s.closeCanvasDocumentAndDockedTab,
  );

  // Painel do workbench: visível e redimensionável via workbench-store. O gate
  // de hidratação evita divergência SSR/cliente do estado persistido.
  const hydrated = useHydrated();
  // Abaixo de Tailwind `sm`, o modo IDE usa o estado mobile e mostra um painel
  // por vez. A largura é medida sem a escala visual do Electron.
  const isNarrowViewport = useIsNarrowViewport();
  const sessionLayoutState = useSessionLayoutState();
  const isCompactSession = sessionLayoutState === "compact";
  const ideLayoutState = useIdeLayoutState();
  const workbenchOpen = useWorkbenchStore((s) => s.isOpen(threadId));
  const setWorkbenchOpen = useWorkbenchStore((s) => s.setPanelOpen);
  const setSplitSize = useWorkbenchStore((s) => s.setSplitSize);
  const openWorkbench = useCallback(
    () => setWorkbenchOpen(threadId, true),
    [setWorkbenchOpen, threadId],
  );

  // CI em tempo real: webhook do GitHub → toast + badge no git-tab (sem F5).
  useWebhookWorkbench();

  // Largura da sidebar (desktop) arrastável pela borda direita.
  const setSidebarWidth = useSettingsStore((s) => s.setSidebarWidth);
  const sidebarPosition = useSettingsStore((s) => s.sidebarPosition);
  const sidebarOnRight = sidebarPosition === "right";
  const assistantWorkbenchSide = sidebarOnRight ? "left" : "right";
  const ideWorkbenchSide = sidebarOnRight ? "right" : "left";
  const chatMode = useSettingsStore((s) => s.chatMode);
  const reducedMotion = useReducedMotion();
  const assistantWorkbenchVisible = hydrated && !chatMode && workbenchOpen;
  const [chatSidebarOpen, setChatSidebarOpen] = useState(true);
  const setChatMode = useSettingsStore((s) => s.setChatMode);
  const uiMode = useSettingsStore((s) => s.uiMode);
  const activeWorkbenchSide =
    uiMode === "ide" ? ideWorkbenchSide : assistantWorkbenchSide;
  const setChatSidebarWidth = useSettingsStore((s) => s.setChatSidebarWidth);
  const { sidebarWidth, chatSidebarWidth, splitSize } = useClampPanelWidths();
  // Modelo do chat — lido do store persistido (sobrevive a restart/reload).
  const selectedModel = useSettingsStore((s) => s.selectedModel);
  const setSelectedModel = useSettingsStore((s) => s.setSelectedModel);
  const draggingSidebar = useRef(false);

  // Resize do painel de workbench content no modo IDE (borda direita do painel)
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
      const rect = e.currentTarget.parentElement?.getBoundingClientRect();
      if (rect) {
        const width = getPanelWidthFromPointer(
          e.clientX,
          rect,
          activeWorkbenchSide,
        );
        setSplitSize(
          Math.min(
            WORKBENCH_CONTENT_MAX_WIDTH,
            Math.max(WORKBENCH_CONTENT_MIN_WIDTH, width - WORKBENCH_RAIL_WIDTH),
          ),
        );
      }
    },
    [activeWorkbenchSide, setSplitSize],
  );
  const onWorkbenchResizeKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      setSplitSize(
        Math.min(
          480,
          Math.max(
            WORKBENCH_CONTENT_MIN_WIDTH,
            splitSize + getResizeDelta(e.key, activeWorkbenchSide),
          ),
        ),
      );
    },
    [activeWorkbenchSide, setSplitSize, splitSize],
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
      const rect = e.currentTarget.parentElement?.getBoundingClientRect();
      if (rect) {
        const width = getPanelWidthFromPointer(
          e.clientX,
          rect,
          // O chat fica à esquerda quando a composição é RTL e à direita
          // quando é LTR; o divisor acompanha a borda interna correspondente.
          sidebarOnRight ? "left" : "right",
        );
        setChatSidebarWidth(
          Math.min(520, Math.max(CHAT_SIDEBAR_OPEN_MIN_WIDTH, width)),
        );
      }
    },
    [setChatSidebarWidth, sidebarOnRight],
  );
  const onChatSidebarResizeKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      setChatSidebarWidth(
        Math.min(
          520,
          Math.max(
            CHAT_SIDEBAR_OPEN_MIN_WIDTH,
            chatSidebarWidth +
              getResizeDelta(e.key, sidebarOnRight ? "left" : "right"),
          ),
        ),
      );
    },
    [chatSidebarWidth, setChatSidebarWidth, sidebarOnRight],
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
      const rect = e.currentTarget.parentElement?.getBoundingClientRect();
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
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);

  useEffect(() => {
    if (!isCompactSession) {
      // A Sheet opened in compact mode must not remain over the desktop shell
      // after a resize back to the wide layout.
      // oxlint-disable-next-line react/set-state-in-effect
      setIsMobileSidebarOpen(false);
    }
  }, [isCompactSession]);
  const [showToolCalls, setShowToolCalls] = useState(false);
  const [showShortcutsDialog, setShowShortcutsDialog] = useState(false);
  const [showCommandPalette, setShowCommandPalette] = useState(false);
  const [showNewChatDialog, setShowNewChatDialog] = useState(false);
  const [inputLocked, setInputLocked] = useState(false);
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
  const isNewSession = useIsNewThread(threadId);
  const workspaceChosen = useIsWorkspaceChosen(threadId);

  // Threads do workspace ativo (para o session switcher do IDE mode).
  const activeWorkspaceId = useWorkspacesStore((s) => s.active_id);
  const visibleCanvasDocuments = useMemo(
    () =>
      canvasDocuments.filter((document) =>
        isCanvasDocumentVisible(document, activeWorkspaceId, threadId),
      ),
    [canvasDocuments, activeWorkspaceId, threadId],
  );
  const visibleActiveCanvasDocumentId =
    activeCanvasDocumentId &&
    visibleCanvasDocuments.some(
      (document) => document.id === activeCanvasDocumentId,
    )
      ? activeCanvasDocumentId
      : (visibleCanvasDocuments[0]?.id ?? "editor");
  const handleOpenEditedFile = useCallback(
    (file: EditedFile) => {
      if (!activeWorkspaceId) return;
      openCanvasDocument({
        id: `file-diff:${activeWorkspaceId}:${threadId}:${file.path}`,
        kind: "file-diff",
        workspaceId: activeWorkspaceId,
        threadId,
        title: file.path.split(/[\\/]/).pop() ?? file.path,
        path: file.path,
        editedFile: file,
      });
    },
    [activeWorkspaceId, openCanvasDocument, threadId],
  );
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

  // O shell é o dono da largura e do handle de resize. O painel fornece
  // somente o conteúdo para que todos os modos usem o mesmo contrato.
  const sidebarPanel = useMemo(
    () => <div className="hidden h-full min-w-0 md:flex">{sidebar}</div>,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sidebar],
  );

  const headerEl = useMemo(
    () => (
      <Header
        showToolCalls={showToolCalls}
        onToggleToolCalls={() => setShowToolCalls((v) => !v)}
        onShowShortcuts={() => setShowShortcutsDialog(true)}
        onOpenSidebar={
          uiMode === "ide" || !isCompactSession
            ? undefined
            : () => setIsMobileSidebarOpen(true)
        }
        sidebarTriggerCompactOnly={uiMode !== "ide" && isCompactSession}
        showModeSwitch={!chatMode}
      />
    ),
    [showToolCalls, chatMode, uiMode, isCompactSession],
  );

  // Cada modo escolhe explicitamente a coluna esquerda. Assistente e Kanban
  // usam a lista de sessões; IDE usa a workbench. Manter a sidebar de sessões
  // fora do IDE evitava que o shell tivesse quatro colunas concorrentes.
  const showSidebarPanel = uiMode !== "ide" && !isCompactSession;
  // A sessão e o IDE usam o mesmo contrato de largura do shell: a coluna
  // recolhida mede exatamente a rail, sem herdar o piso da coluna aberta.
  const sessionSidebarWidth = isSidebarCollapsed
    ? SIDEBAR_COLLAPSED_WIDTH
    : hydrated
      ? sidebarWidth
      : 240;
  const modeComposition = getModeComposition(uiMode);

  // Chat renderizado no fluxo normal do layout de cada modo. `compact`
  // (IDE) muda densidade e liga o SessionSwitcher acima dele; a posição
  // do scroll sobrevive à troca de modo via `message-list.tsx`, que
  // guarda e restaura por thread — não por manter a instância montada.
  const renderChatPanel = useCallback(
    (compact: boolean, onCollapse?: () => void) => {
      const welcomeActions =
        !compact && hydrated && isNewRoute && !workspaceChosen;
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
              onOpenEditedFile={handleOpenEditedFile}
              inputLocked={inputLocked}
              // The route is already known to be new during the first render,
              // before useNewSessionId's committed effect registers the local
              // id. Keep the history loader from treating that id as persisted.
              isNewThread={isNewRoute || isNew(threadId) || isNewSession}
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
      isNewSession,
      workspaceChosen,
      handleStartChatFromWelcome,
      handleOpenEditedFile,
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
        {uiMode !== "ide" && (
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
                left={showSidebarPanel ? sidebarPanel : null}
                center={
                  <div className="flex min-w-[360px] flex-1 min-h-0 overflow-hidden">
                    <KanbanBoard threadId={threadId} />
                  </div>
                }
                right={null}
                columns={{
                  left: {
                    label: "Sessões",
                    visibility: showSidebarPanel ? "visible" : "hidden",
                    width: showSidebarPanel ? sessionSidebarWidth : undefined,
                    minWidth: showSidebarPanel
                      ? sessionSidebarWidth
                      : undefined,
                    resize:
                      showSidebarPanel && !isSidebarCollapsed
                        ? {
                            ariaLabel: m.resize_sidebar(),
                            value: sidebarWidth,
                            min: SIDE_COLUMN_MIN_WIDTH,
                            max: 520,
                            onKeyDown: (e) => {
                              if (
                                e.key !== "ArrowLeft" &&
                                e.key !== "ArrowRight"
                              )
                                return;
                              e.preventDefault();
                              setSidebarWidth(
                                Math.min(
                                  520,
                                  Math.max(
                                    SIDE_COLUMN_MIN_WIDTH,
                                    sidebarWidth +
                                      getResizeDelta(
                                        e.key,
                                        sidebarOnRight ? "right" : "left",
                                      ),
                                  ),
                                ),
                              );
                            },
                            onPointerDown: onSidebarResizeDown,
                            onPointerMove: onSidebarResizeMove,
                            onPointerUp: onSidebarResizeUp,
                            onPointerCancel: onSidebarResizeUp,
                          }
                        : undefined,
                  },
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
                onOpenWorkbench={openWorkbench}
                layoutState={isCompactSession ? "mobile" : ideLayoutState}
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
                chatWidth={hydrated ? chatSidebarWidth : 256}
                chatMinWidth={CHAT_SIDEBAR_OPEN_MIN_WIDTH}
                chatMaxWidth={520}
                workbenchResize={
                  !isCompactSession && workbenchOpen
                    ? {
                        ariaLabel: m.resize_workbench(),
                        value: splitSize,
                        min: WORKBENCH_CONTENT_MIN_WIDTH,
                        max: WORKBENCH_CONTENT_MAX_WIDTH,
                        onKeyDown: onWorkbenchResizeKeyDown,
                        onPointerDown: onWorkbenchResizeDown,
                        onPointerMove: onWorkbenchResizeMove,
                        onPointerUp: onWorkbenchResizeUp,
                        onPointerCancel: onWorkbenchResizeUp,
                      }
                    : undefined
                }
                chatResize={
                  !isCompactSession
                    ? {
                        ariaLabel: m.resize_chat(),
                        value: chatSidebarWidth,
                        min: CHAT_SIDEBAR_OPEN_MIN_WIDTH,
                        max: 520,
                        onKeyDown: onChatSidebarResizeKeyDown,
                        onPointerDown: onChatSidebarResizeDown,
                        onPointerMove: onChatSidebarResizeMove,
                        onPointerUp: onChatSidebarResizeUp,
                        onPointerCancel: onChatSidebarResizeUp,
                      }
                    : undefined
                }
                showChat={chatSidebarOpen}
                onOpenChat={() => setChatSidebarOpen(true)}
                header={headerEl}
                navBar={
                  <WorkbenchNavBar
                    threadId={threadId}
                    side={ideWorkbenchSide}
                  />
                }
                workbenchContent={
                  <div
                    className="relative flex-1 min-w-0 overflow-hidden"
                    aria-hidden={!workbenchOpen}
                  >
                    <WorkbenchContent
                      threadId={threadId}
                      side={ideWorkbenchSide}
                      visible={hydrated && workbenchOpen}
                      onAddToContext={pushMention}
                      onSendPrompt={pushDraft}
                    />
                  </div>
                }
                editor={
                  // min-w-[360px]: piso mínimo pro editor continuar usável
                  // ao encolher a janela ou puxar o painel do workbench largo.
                  <div className="flex flex-col flex-1 min-w-0 h-full overflow-hidden">
                    <CenterCanvas
                      documents={visibleCanvasDocuments}
                      activeTab={visibleActiveCanvasDocumentId}
                      onTabChange={activateCanvasDocument}
                      onTabClose={closeCanvasDocument}
                      renderDocument={(document) =>
                        document.kind === "file" &&
                        document.workspaceId &&
                        document.path ? (
                          <FileEditor
                            workspaceId={document.workspaceId!}
                            path={document.path}
                          />
                        ) : document.kind === "file-diff" &&
                          document.editedFile ? (
                          <CanvasFileDiff editedFile={document.editedFile} />
                        ) : document.kind === "mcp-preview" && document.mcp ? (
                          <LibraryMcpPreview mcp={document.mcp} />
                        ) : null
                      }
                    >
                      <DockedEditor activeWorkspaceId={activeWorkspaceId} />
                    </CenterCanvas>
                  </div>
                }
                chat={
                  <div
                    className={
                      isCompactSession
                        ? "relative flex min-w-0 flex-col h-full bg-sidebar"
                        : `relative flex min-w-0 flex-col h-full border-border/60 bg-sidebar ${sidebarOnRight ? "border-r" : "border-l"}`
                    }
                  >
                    <div className="flex-1 min-h-0 min-w-0">
                      {renderChatPanel(
                        true,
                        !isCompactSession
                          ? () => setChatSidebarOpen(false)
                          : undefined,
                      )}
                    </div>
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
                left={showSidebarPanel ? sidebarPanel : null}
                center={
                  <div className="flex flex-1 min-w-0 min-h-0 flex-col overflow-hidden">
                    {isCompactSession && assistantWorkbenchVisible ? (
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
                    className={`relative flex h-full min-w-0 min-h-0 flex-1 flex-row overflow-hidden border-border/60 ${assistantWorkbenchSide === "right" ? "border-l" : "border-r"}`}
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
                        side={assistantWorkbenchSide}
                        visible
                        onAddToContext={pushMention}
                        onSendPrompt={pushDraft}
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
                showRight={hydrated && !chatMode && !isCompactSession}
                direction={sidebarOnRight ? "rtl" : "ltr"}
                columns={{
                  left: {
                    label: "Sessões",
                    visibility: showSidebarPanel ? "visible" : "hidden",
                    width: showSidebarPanel ? sessionSidebarWidth : undefined,
                    minWidth: showSidebarPanel
                      ? sessionSidebarWidth
                      : undefined,
                    resize:
                      showSidebarPanel && !isSidebarCollapsed
                        ? {
                            ariaLabel: m.resize_sidebar(),
                            value: sidebarWidth,
                            min: SIDE_COLUMN_MIN_WIDTH,
                            max: 520,
                            onKeyDown: (e) => {
                              if (
                                e.key !== "ArrowLeft" &&
                                e.key !== "ArrowRight"
                              )
                                return;
                              e.preventDefault();
                              setSidebarWidth(
                                Math.min(
                                  520,
                                  Math.max(
                                    SIDE_COLUMN_MIN_WIDTH,
                                    sidebarWidth +
                                      getResizeDelta(
                                        e.key,
                                        sidebarOnRight ? "right" : "left",
                                      ),
                                  ),
                                ),
                              );
                            },
                            onPointerDown: onSidebarResizeDown,
                            onPointerMove: onSidebarResizeMove,
                            onPointerUp: onSidebarResizeUp,
                            onPointerCancel: onSidebarResizeUp,
                          }
                        : undefined,
                  },
                  center: { label: "Chat" },
                  right: assistantWorkbenchVisible
                    ? {
                        label: "Workbench",
                        visibility: "visible",
                        width: splitSize + WORKBENCH_RAIL_WIDTH,
                        minWidth:
                          WORKBENCH_CONTENT_MIN_WIDTH + WORKBENCH_RAIL_WIDTH,
                        maxWidth:
                          WORKBENCH_CONTENT_MAX_WIDTH + WORKBENCH_RAIL_WIDTH,
                        resize: {
                          ariaLabel: m.resize_workbench(),
                          value: splitSize,
                          min: WORKBENCH_CONTENT_MIN_WIDTH,
                          max: WORKBENCH_CONTENT_MAX_WIDTH,
                          onKeyDown: onWorkbenchResizeKeyDown,
                          onPointerDown: onWorkbenchResizeDown,
                          onPointerMove: onWorkbenchResizeMove,
                          onPointerUp: onWorkbenchResizeUp,
                          onPointerCancel: onWorkbenchResizeUp,
                        },
                      }
                    : {
                        label: "Workbench",
                        visibility: "collapsed",
                        onExpand: openWorkbench,
                        expandLabel: m.layout_open_workbench(),
                      },
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
      </div>
    </div>
  );
}
