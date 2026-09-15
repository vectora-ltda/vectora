"use client";

/**
 * Painel lateral direito do workbench, dividido em 2 partes (estilo VS Code):
 *
 * - `WorkbenchNavBar` — faixa estreita (48px) sempre visível, com os ícones
 *   das abas (Arquivos, Diff, Plano, Terminal). Não é redimensionável.
 *   Clicar numa aba já ativa com o painel aberto colapsa o painel; clicar em
 *   outra aba (ou com o painel fechado) troca/abre.
 * - `WorkbenchContent` — painel de conteúdo da aba ativa, redimensionável,
 *   só renderizado quando o painel está aberto (`isOpen`).
 *
 * Cada aba mostra um chip de contagem lido do cache do workbench-store (não
 * dispara fetch só para contar):
 *   - terminal: número de PTYs abertos na sessão
 *   - files: número de arquivos fixados
 *   - diff: `+N -M` quando há mudanças
 *   - plan: número de artifacts na sessão
 *
 * Antes de hidratar, o badge fica vazio (consistente com `useHydrated`) para
 * evitar divergência SSR/cliente.
 */

import {
  FileText,
  FolderTree,
  GitCompare,
  TerminalSquare,
  X,
  MonitorPlay,
  Brain,
  Radar,
  Waypoints,
  Library,
  Package,
} from "lucide-react";
import { useEffect } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useWorkspaceWatcher } from "@/lib/hooks/use-workspace-watcher";
import { useHydrated } from "@/lib/hooks/use-hydrated";
import {
  useWorkbenchStore,
  WORKBENCH_TABS,
  type WorkbenchTab,
} from "@/lib/stores/workbench-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { TerminalPanel } from "@/components/workbench/terminal/terminal-panel";
import { FilesTab } from "./files/files-tab";
import { GitTab } from "./git/git-tab";
import { PlanTab } from "./tabs/plan-tab";
import { BrowserTab } from "./tabs/browser-tab";
import { MemoryTab } from "./tabs/memory-tab";
import { TasksTab } from "./tabs/tasks-tab";
import { ContextGraphTab } from "./tabs/context-graph-tab";
import { LibraryTab } from "./tabs/library-tab";
import { m } from "@/lib/paraglide/messages";
import { mDyn } from "@/lib/i18n-dyn";
import {
  NATIVE_EXTENSION_IDS,
  useLibraryStore,
  type VextExtension,
} from "@/lib/stores/library-store";
import { ColumnHeader } from "@/components/layout/column-header";
import { RailToggleButton } from "@/components/layout/rail-toggle-button";
import {
  PANEL_TRANSITION,
  PENDING_BADGE_TRANSITION,
} from "@/lib/motion/transitions";

interface WorkbenchPanelProps {
  threadId: string;
  /** Injetar @path no chat ao clicar no botão @ de um arquivo/pasta. */
  onAddToContext?: (path: string) => void;
  /** Inserir texto no composer (god nodes/perguntas sugeridas do Context Graph). */
  onSendPrompt?: (text: string) => void;
}

const TAB_ICON: Record<
  WorkbenchTab,
  React.ComponentType<{ className?: string }>
> = {
  terminal: TerminalSquare,
  files: FolderTree,
  diff: GitCompare,
  plan: FileText,
  browser: MonitorPlay,
  storage: Brain,
  tasks: Radar,
  context_graph: Waypoints,
  library: Library,
};

function ExtensionWorkbench({ extension }: { extension: VextExtension }) {
  if (!extension.frontend_entrypoint) return null;
  return (
    <iframe
      title={extension.name}
      src={`/vext/${encodeURIComponent(extension.id)}/frontend`}
      sandbox="allow-scripts"
      className="h-full w-full border-0 bg-background"
      data-testid={`vext-workbench-${extension.id}`}
    />
  );
}

/** Lê o cache do workbench-store e devolve o texto do chip por aba. */
function useTabBadge(
  threadId: string,
  workspaceId: string,
  tab: WorkbenchTab,
  hydrated: boolean,
): string | null {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  // Selectors são chamados incondicionalmente — Rules of Hooks. Filtramos por
  // aba no return e gateamos por `hydrated` para evitar mismatch SSR (T11).
  const terminals = useWorkbenchStore((s) => s.list(threadId));
  const pinned = useWorkbenchStore((s) => s.pinnedFiles[threadId]?.length ?? 0);
  const planItems = useWorkbenchStore((s) => s.getPlan(threadId).items.length);
  void workspaceId;

  if (!hydrated) return null;
  switch (tab) {
    case "terminal":
      return terminals.length > 0 ? String(terminals.length) : null;
    case "files":
      return pinned > 0 ? String(pinned) : null;
    case "diff":
      // Sem chip de +N −M: o contador de diff poluía mais do que ajudava;
      // o ponto âmbar de "pending" continua sinalizando mudanças.
      return null;
    case "plan":
      return planItems > 0 ? String(planItems) : null;
    case "browser":
    case "storage":
    case "tasks":
    case "context_graph":
    case "library":
      return null;
  }
}

function NavTabButton({
  tab,
  active,
  threadId,
  workspaceId,
  hydrated,
  onSelect,
}: {
  tab: WorkbenchTab;
  active: boolean;
  threadId: string;
  workspaceId: string;
  hydrated: boolean;
  onSelect: () => void;
}) {
  const reducedMotion = useReducedMotion();
  const badge = useTabBadge(threadId, workspaceId, tab, hydrated);
  const pending = useWorkbenchStore((s) =>
    tab === "files"
      ? Boolean(s.pending[workspaceId]?.files)
      : tab === "diff"
        ? Boolean(s.pending[workspaceId]?.diff)
        : false,
  );
  const showPending = hydrated && pending && !active;
  const Icon = TAB_ICON[tab];
  const label = mDyn(`workbench.tab.${tab}`);
  const tooltipText = badge ? `${label} (${badge})` : label;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          onClick={onSelect}
          data-testid={`workbench-nav-${tab}`}
          className={`relative flex items-center justify-center w-8 h-8 rounded-md transition-colors ${
            active
              ? "bg-muted text-foreground"
              : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
          }`}
        >
          <Icon className="w-4 h-4" />
          <AnimatePresence initial={false}>
            {showPending && (
              <motion.span
                key="pending-dot"
                initial={{ scale: 0, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0, opacity: 0 }}
                transition={
                  reducedMotion ? { duration: 0 } : PENDING_BADGE_TRANSITION
                }
                className="absolute top-0.5 right-0.5 w-1.5 h-1.5 rounded-full bg-amber-500"
                aria-label={m.workbench_tab_pending()}
              />
            )}
          </AnimatePresence>
          {badge && (
            <span className="absolute -bottom-1 -right-1 inline-flex items-center justify-center min-w-[1rem] h-4 px-1 rounded-full text-[9px] font-mono leading-none bg-primary/15 text-primary">
              {badge}
            </span>
          )}
        </button>
      </TooltipTrigger>
      <TooltipContent side="right">{tooltipText}</TooltipContent>
    </Tooltip>
  );
}

/**
 * Faixa estreita (48px), sempre visível, com os ícones de cada aba —
 * equivalente à Activity Bar do VS Code. Não é redimensionável.
 *
 * `side="right"` (padrão): layout Assistente, borda esquerda.
 * `side="left"`: layout IDE, borda direita (editor fica à direita).
 * Em ambos, o spacer h-16 é obrigatório — `WorkbenchContent` sempre desenha
 * seu próprio header de h-16 (linha ~292), independente do `side`; sem o
 * spacer aqui os ícones da NavBar ficam ~56px acima de onde o conteúdo do
 * painel de fato começa (bug real observado no modo IDE).
 */
export function WorkbenchNavBar({
  threadId,
  side = "right",
}: {
  threadId: string;
  side?: "left" | "right";
}) {
  const hydrated = useHydrated();
  const workspace = useWorkspacesStore((s) => s.getActive());
  const wsId = workspace?.id ?? "";
  const activeTab = useWorkbenchStore((s) => s.getActiveTab(threadId));
  const activeExtension = useWorkbenchStore((s) =>
    s.getActiveExtension(threadId),
  );
  const isOpen = useWorkbenchStore((s) => s.isOpen(threadId));
  const selectTab = useWorkbenchStore((s) => s.selectTab);
  const setPanelOpen = useWorkbenchStore((s) => s.setPanelOpen);
  const selectExtension = useWorkbenchStore((s) => s.selectExtension);
  const extensionItems = useLibraryStore((s) => s.extensionItems);
  const ensureExtensionsLoaded = useLibraryStore(
    (s) => s.ensureExtensionsLoaded,
  );
  useEffect(() => {
    void ensureExtensionsLoaded();
  }, [ensureExtensionsLoaded]);
  const workbenchExtensions = extensionItems.filter(
    (item) =>
      !item.native &&
      !NATIVE_EXTENSION_IDS.has(item.id) &&
      item.frontend_entrypoint &&
      item.contributions?.workbench?.some(
        (contribution) => contribution.entrypoint,
      ),
  );

  return (
    <div
      className={`h-full w-12 shrink-0 flex flex-col items-center bg-sidebar ${
        side === "left" ? "border-r" : "border-l"
      } border-border/60`}
    >
      {/* Spacer h-16: alinha os ícones com a base do header de WorkbenchContent
          (sempre h-16, nos dois modos — ver comentário do JSDoc acima). */}
      <div
        data-testid="workbench-header-spacer"
        className="h-[var(--app-header-height)] w-full shrink-0 border-b border-border/60 flex items-center justify-center"
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <RailToggleButton
              side={side}
              onClick={() => setPanelOpen(threadId, !isOpen)}
              data-testid="workbench-collapse"
              ariaLabel={m.workbench_toggle()}
              ariaExpanded={isOpen}
            />
          </TooltipTrigger>
          <TooltipContent side={side === "left" ? "right" : "left"}>
            {m.workbench_toggle()}
          </TooltipContent>
        </Tooltip>
      </div>
      <div className="flex flex-col items-center gap-1 pt-2">
        {WORKBENCH_TABS.map((tab) => (
          <NavTabButton
            key={tab}
            tab={tab}
            active={hydrated && isOpen && !activeExtension && tab === activeTab}
            threadId={threadId}
            workspaceId={wsId}
            hydrated={hydrated}
            onSelect={() => selectTab(threadId, tab)}
          />
        ))}
        {workbenchExtensions.map((extension) => (
          <Tooltip key={extension.id}>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => selectExtension(threadId, extension.id)}
                data-testid={`workbench-nav-extension-${extension.id}`}
                aria-label={extension.name}
                className={`relative flex items-center justify-center w-8 h-8 rounded-md transition-colors ${
                  activeExtension === extension.id && isOpen
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                }`}
              >
                <Package className="w-4 h-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">{extension.name}</TooltipContent>
          </Tooltip>
        ))}
      </div>
    </div>
  );
}

/**
 * Conteúdo da aba ativa — redimensionável, montado apenas quando o painel
 * está aberto (`isOpen`). Vive ao lado (à esquerda, na ordem visual) da
 * `WorkbenchNavBar`.
 *
 * `side="right"` (padrão): layout Assistente, borda esquerda.
 * `side="left"`: layout IDE, borda direita (editor fica à direita do painel).
 */
export function WorkbenchContent({
  threadId,
  onAddToContext,
  onSendPrompt,
  side = "right",
  visible = true,
}: WorkbenchPanelProps & { side?: "left" | "right"; visible?: boolean }) {
  const workspace = useWorkspacesStore((s) => s.getActive());
  const wsId = workspace?.id ?? "";
  const activeTab = useWorkbenchStore((s) => s.getActiveTab(threadId));
  const activeExtension = useWorkbenchStore((s) =>
    s.getActiveExtension(threadId),
  );
  const setPanelOpen = useWorkbenchStore((s) => s.setPanelOpen);
  const extensionItems = useLibraryStore((s) => s.extensionItems);
  const ensureExtensionsLoaded = useLibraryStore(
    (s) => s.ensureExtensionsLoaded,
  );
  const ActiveIcon = TAB_ICON[activeTab];
  const reducedMotion = useReducedMotion();

  // A.17 — file watcher SSE: dispara markPending quando arquivos mudam
  useWorkspaceWatcher(wsId || undefined);
  useEffect(() => {
    void ensureExtensionsLoaded();
  }, [ensureExtensionsLoaded]);
  const extension = activeExtension
    ? extensionItems.find((item) => item.id === activeExtension)
    : undefined;

  return (
    <div
      className={`h-full flex flex-1 min-w-0 flex-col bg-sidebar ${
        side === "left" ? "border-r" : "border-l"
      } border-border/60`}
    >
      <ColumnHeader className="justify-between px-3">
        <span
          className="flex items-center gap-2 text-sm font-medium"
          data-testid="workbench-header-title"
          data-active-tab={activeTab}
        >
          <ActiveIcon className="w-4 h-4 text-muted-foreground" />
          {extension?.name ?? mDyn(`workbench.tab.${activeTab}`)}
        </span>
        <div className="flex items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={() => setPanelOpen(threadId, false)}
                aria-label={m.workbench_close()}
                className="flex items-center justify-center w-7 h-7 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="left">{m.workbench_close()}</TooltipContent>
          </Tooltip>
        </div>
      </ColumnHeader>

      {/* Body — só monta a aba ativa (poupa recurso); entrada com slide suave.
          Sem AnimatePresence: a troca de aba não pode depender de uma
          animação de saída completar — em produção essa transição de exit
          ficou presa (framer-motion nunca chamava onExitComplete), travando
          o conteúdo renderizado na primeira aba montada para sempre enquanto
          só o header seguia trocando. Unmount/mount é instantâneo agora;
          só a entrada anima. */}
      <div className="flex-1 min-h-0 overflow-hidden relative">
        <motion.div
          key={activeExtension ?? activeTab}
          initial={reducedMotion ? false : { opacity: 0, x: 6 }}
          animate={{ opacity: 1, x: 0 }}
          transition={reducedMotion ? { duration: 0 } : PANEL_TRANSITION}
          className="absolute inset-0"
          data-testid="workbench-tab-content"
          data-tab={activeTab}
        >
          {extension ? (
            <ExtensionWorkbench extension={extension} />
          ) : (
            <>
              {activeTab === "terminal" && (
                <TerminalPanel threadId={threadId} />
              )}
              {activeTab === "files" && (
                <FilesTab threadId={threadId} onAddToContext={onAddToContext} />
              )}
              {activeTab === "diff" && <GitTab threadId={threadId} />}
              {activeTab === "plan" && <PlanTab threadId={threadId} />}
              {activeTab === "browser" && (
                <BrowserTab
                  key={`${wsId}:${threadId}`}
                  threadId={threadId}
                  visible={visible}
                />
              )}
              {activeTab === "storage" && <MemoryTab threadId={threadId} />}
              {activeTab === "tasks" && <TasksTab threadId={threadId} />}
              {activeTab === "context_graph" && (
                <ContextGraphTab
                  threadId={threadId}
                  onSendPrompt={onSendPrompt}
                />
              )}
              {activeTab === "library" && <LibraryTab threadId={threadId} />}
            </>
          )}
        </motion.div>
      </div>
    </div>
  );
}
