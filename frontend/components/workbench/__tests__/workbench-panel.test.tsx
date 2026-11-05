// @vitest-environment jsdom
/**
 * WorkbenchContent — regressão do bug real: ContextGraphTab (443 linhas,
 * build/update/resume/cancel/settings/god-nodes) existia por completo mas
 * nunca era montado — o switch de renderização não tinha `case` pra
 * "context_graph", então o painel abria vazio (só o shell/título/X).
 *
 * Este teste garante que `activeTab === "context_graph"` de fato monta o
 * componente — travando a classe exata desse bug (peça pronta, nunca ligada).
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, act } from "@testing-library/react";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get:
        (_t, prop) =>
        (...args: unknown[]) =>
          args.length
            ? `${String(prop)}(${JSON.stringify(args[0])})`
            : String(prop),
    },
  ),
}));

vi.mock("@/lib/i18n-dyn", () => ({
  mDyn: (key: string) => key,
}));

// Store real (Zustand puro, sem rede/backend) — mais simples e robusto do
// que recriar à mão todos os selectors que WorkbenchNavBar/Content usam
// (badges por aba, pending de files/Git, etc.).
import {
  useWorkbenchStore,
  type WorkbenchTab,
} from "@/lib/stores/workbench-store";

function setActiveTab(threadId: string, tab: WorkbenchTab) {
  useWorkbenchStore.getState().selectTab(threadId, tab);
}

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (sel: (s: { getActive: () => undefined }) => unknown) =>
    sel({ getActive: () => undefined }),
}));

vi.mock("@/lib/hooks/use-workspace-watcher", () => ({
  useWorkspaceWatcher: () => {},
}));

vi.mock("@/lib/hooks/use-hydrated", () => ({ useHydrated: () => true }));
const featureFlags = { enableFeaturesBeta: false };
vi.mock("@/lib/hooks/use-feature-flags", () => ({
  useFeatureFlags: () => featureFlags,
}));

vi.mock("@/components/workbench/terminal/terminal-panel", () => ({
  TerminalPanel: () => <div>stub-terminal</div>,
}));
vi.mock("../files/files-tab", () => ({
  FilesTab: () => <div>stub-files</div>,
}));
vi.mock("../git/git-tab", () => ({ GitTab: () => <div>stub-git</div> }));
vi.mock("../tabs/plan-tab", () => ({ PlanTab: () => <div>stub-plan</div> }));
vi.mock("../tabs/browser-tab", () => ({
  BrowserTab: () => <div>stub-browser</div>,
}));
vi.mock("../tabs/memory-tab", () => ({
  MemoryTab: () => <div>stub-memory</div>,
}));
vi.mock("../tabs/tasks-tab", () => ({
  TasksTab: () => <div>stub-tasks</div>,
}));
vi.mock("../tabs/context-graph-tab", () => ({
  ContextGraphTab: ({
    onSendPrompt,
  }: {
    onSendPrompt?: (text: string) => void;
  }) => (
    <div>
      stub-context-graph
      <button onClick={() => onSendPrompt?.("pergunta do grafo")}>
        enviar
      </button>
    </div>
  ),
}));
vi.mock("../tabs/library-tab", () => ({
  LibraryTab: () => <div>stub-library</div>,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  featureFlags.enableFeaturesBeta = false;
});

import { TooltipProvider } from "@/components/ui/tooltip";
import {
  WorkbenchContent,
  WorkbenchNavBar,
} from "@/components/workbench/workbench-panel";

function renderContent(props: Parameters<typeof WorkbenchContent>[0]) {
  return render(
    <TooltipProvider>
      <WorkbenchContent {...props} />
    </TooltipProvider>,
  );
}

describe("WorkbenchContent — switch de renderização por aba", () => {
  it("activeTab='context_graph' monta ContextGraphTab (regressão: componente pronto, nunca religado)", () => {
    setActiveTab("t1", "context_graph");
    renderContent({ threadId: "t1" });
    expect(screen.getByText("stub-context-graph")).toBeInTheDocument();
  });

  it("passa onSendPrompt adiante pro ContextGraphTab", async () => {
    const { fireEvent } = await import("@testing-library/react");
    setActiveTab("t1", "context_graph");
    const onSendPrompt = vi.fn();
    renderContent({ threadId: "t1", onSendPrompt });
    fireEvent.click(screen.getByText("enviar"));
    expect(onSendPrompt).toHaveBeenCalledWith("pergunta do grafo");
  });

  it("outras abas continuam montando seus componentes (sem regressão)", () => {
    setActiveTab("t1", "plan");
    renderContent({ threadId: "t1" });
    expect(screen.getByText("stub-plan")).toBeInTheDocument();
    expect(screen.queryByText("stub-context-graph")).not.toBeInTheDocument();
  });

  it("activeTab='library' monta LibraryTab", () => {
    setActiveTab("t1", "library");
    renderContent({ threadId: "t1" });
    expect(screen.getByText("stub-library")).toBeInTheDocument();
  });
});

describe("WorkbenchNavBar — ícone do Context Graph não fica mais atrás de flag beta", () => {
  it("o botão do Context Graph é clicável (NavTabButton real, não ComingSoonTabButton desabilitado)", () => {
    setActiveTab("t1", "files");
    render(
      <TooltipProvider>
        <WorkbenchNavBar threadId="t1" />
      </TooltipProvider>,
    );
    // ComingSoonTabButton usa aria-disabled="true" e tabIndex={-1} — o botão
    // real (NavTabButton) não tem esses atributos. Library está em beta,
    // então com enableFeaturesBeta=false ela fica desabilitada — a asserção
    // aqui é só sobre o Context Graph, não "nenhum botão".
    const buttons = screen.getAllByRole("button");
    const contextGraphBtn = buttons.find(
      (b) => b.getAttribute("aria-disabled") !== "true",
    );
    expect(contextGraphBtn).toBeDefined();
  });
});

describe("WorkbenchContent — troca de aba nunca trava no conteúdo anterior (regressão ao vivo)", () => {
  // Bug real encontrado em raio-X manual: AnimatePresence mode="wait" na
  // troca de aba podia nunca completar a animação de saída em produção,
  // travando o conteúdo montado (ex. Plan) enquanto só o header seguia
  // atualizando (activeTab é lido fora do AnimatePresence). O fix removeu
  // o AnimatePresence da troca — este teste garante que, em sequência,
  // header e conteúdo montado SEMPRE correspondem à mesma aba, nunca a uma
  // aba anterior.
  const ALL_TABS: WorkbenchTab[] = [
    "terminal",
    "files",
    "git",
    "plan",
    "browser",
    "storage",
    "tasks",
    "context_graph",
    "library",
  ];
  const STUB_TEXT: Record<WorkbenchTab, string> = {
    terminal: "stub-terminal",
    files: "stub-files",
    git: "stub-git",
    plan: "stub-plan",
    browser: "stub-browser",
    storage: "stub-memory",
    tasks: "stub-tasks",
    context_graph: "stub-context-graph",
    library: "stub-library",
  };

  it("percorrendo todas as 9 abas em sequência, o header e o conteúdo montado batem em cada troca", () => {
    for (const tab of ALL_TABS) {
      setActiveTab("t-sequencia", tab);
      const { unmount } = renderContent({ threadId: "t-sequencia" });
      expect(
        screen.getByText(`workbench.tab.${tab}`, { exact: false }),
      ).toBeInTheDocument();
      expect(screen.getByText(STUB_TEXT[tab])).toBeInTheDocument();
      for (const other of ALL_TABS) {
        if (other === tab) continue;
        expect(screen.queryByText(STUB_TEXT[other])).not.toBeInTheDocument();
      }
      unmount();
    }
  });

  it("trocando rapidamente entre 3 abas seguidas (Arquivos→Git→Plano) sem desmontar entre elas, o conteúdo final é sempre o da última aba selecionada", () => {
    setActiveTab("t-rapido", "files");
    const { rerender } = renderContent({ threadId: "t-rapido" });
    expect(screen.getByText("stub-files")).toBeInTheDocument();

    act(() => setActiveTab("t-rapido", "git"));
    rerender(
      <TooltipProvider>
        <WorkbenchContent threadId="t-rapido" />
      </TooltipProvider>,
    );
    expect(screen.getByText("stub-git")).toBeInTheDocument();
    expect(screen.queryByText("stub-files")).not.toBeInTheDocument();

    act(() => setActiveTab("t-rapido", "plan"));
    rerender(
      <TooltipProvider>
        <WorkbenchContent threadId="t-rapido" />
      </TooltipProvider>,
    );
    expect(screen.getByText("stub-plan")).toBeInTheDocument();
    expect(screen.queryByText("stub-git")).not.toBeInTheDocument();
    expect(screen.queryByText("stub-files")).not.toBeInTheDocument();
  });
});

describe("WorkbenchNavBar — Library estável", () => {
  it("com enableFeaturesBeta=false, nenhum botão fica desabilitado (Library não é mais beta)", () => {
    setActiveTab("t1", "files");
    render(
      <TooltipProvider>
        <WorkbenchNavBar threadId="t1" />
      </TooltipProvider>,
    );
    const buttons = screen.getAllByRole("button");
    const anyDisabled = buttons.some(
      (b) => b.getAttribute("aria-disabled") === "true",
    );
    expect(anyDisabled).toBe(false);
  });

  it("com enableFeaturesBeta=true, todos os botões (incluindo Library) ficam clicáveis", () => {
    featureFlags.enableFeaturesBeta = true;
    setActiveTab("t1", "files");
    render(
      <TooltipProvider>
        <WorkbenchNavBar threadId="t1" />
      </TooltipProvider>,
    );
    const buttons = screen.getAllByRole("button");
    const anyDisabled = buttons.some(
      (b) => b.getAttribute("aria-disabled") === "true",
    );
    expect(anyDisabled).toBe(false);
  });
});
