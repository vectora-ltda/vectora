// @vitest-environment jsdom
/**
 * SessionPage ($threadId) — composição de layout por modo de interface.
 *
 * Este arquivo era o menos coberto do frontend (4% de statements) e é
 * justamente onde moraram os bugs de UI mais graves: chat do modo anterior
 * desenhado por cima do Kanban, header roubando a faixa de topo dos painéis
 * no IDE, e conteúdo de dois modos visível ao mesmo tempo. Nenhum deles era
 * pegável por teste unitário antes porque a composição não tinha nenhum.
 *
 * A estratégia é trocar os componentes pesados (chat, kanban, workbench,
 * editor) por marcadores e afirmar QUAIS aparecem, ONDE, e em que
 * quantidade — que é exatamente o contrato que quebrava.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, act } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";

const { navigateSpy } = vi.hoisted(() => ({ navigateSpy: vi.fn() }));

vi.mock("@tanstack/react-router", () => ({
  // O componente lê `Route.useParams()`, então o objeto devolvido por
  // createFileRoute precisa carregá-lo — não basta repassar as opções.
  createFileRoute: () => (opts: Record<string, unknown>) => ({
    ...opts,
    useParams: () => ({ threadId: "t1" }),
  }),
  useNavigate: () => navigateSpy,
}));

vi.mock("@/components/chat/chat-interface", () => ({
  ChatInterface: ({ compact }: { compact?: boolean }) => (
    <div data-testid="chat" data-compact={String(!!compact)} />
  ),
}));
vi.mock("@/components/kanban/kanban-board", () => ({
  KanbanBoard: () => <div data-testid="kanban" />,
}));
vi.mock("@/components/header/header", () => ({
  Header: ({
    onOpenSidebar,
    sidebarTriggerCompactOnly,
  }: {
    onOpenSidebar?: () => void;
    sidebarTriggerCompactOnly?: boolean;
  }) => (
    <div data-testid="header">
      {onOpenSidebar && (
        <button
          type="button"
          data-testid="header-sidebar-trigger"
          data-compact-only={String(Boolean(sidebarTriggerCompactOnly))}
          onClick={onOpenSidebar}
        />
      )}
    </div>
  ),
}));
vi.mock("@/components/header/mode-switcher", () => ({
  ModeSwitch: () => <div data-testid="mode-switch" />,
}));
vi.mock("@/components/sidebar/sidebar", () => ({
  Sidebar: () => <div data-testid="sidebar" />,
}));
vi.mock("@/components/header/session-switcher", () => ({
  SessionSwitcher: () => <div data-testid="session-switcher" />,
}));
vi.mock("@/components/workbench/workbench-panel", () => ({
  WorkbenchContent: () => <div data-testid="workbench-content" />,
  WorkbenchNavBar: () => <div data-testid="workbench-navbar" />,
}));
vi.mock("@/components/workbench/windows/docked-editor", () => ({
  DockedEditor: () => <div data-testid="editor" />,
}));
vi.mock("@/components/workbench/windows/window-layer", () => ({
  WindowLayer: () => null,
}));
vi.mock("@/components/workbench/windows/window-dock", () => ({
  WindowDock: () => null,
}));
vi.mock("@/components/layout/license-banner", () => ({
  LicenseBanner: () => null,
}));
vi.mock("@/components/layout/keyboard-shortcuts-dialog", () => ({
  KeyboardShortcutsDialog: () => null,
}));
vi.mock("@/components/layout/command-palette", () => ({
  CommandPalette: () => null,
}));
vi.mock("@/components/sidebar/new-chat-dialog", () => ({
  NewChatDialog: () => null,
}));

// O IdeModeLayout real traz drag/resize junto; o marcador preserva os SLOTS,
// que é sobre o que os testes de posição do Header afirmam. `slot-center`
// espelha a coluna central real (header + navBar + workbenchContent +
// editor) — `chat` fica FORA dela, como a coluna lateral direita do IDE.
vi.mock("@/components/layout/ide-mode-layout", () => ({
  IdeModeLayout: ({
    header,
    navBar,
    workbenchContent,
    editor,
    chat,
  }: {
    header: ReactNode;
    navBar: ReactNode;
    workbenchContent: ReactNode;
    editor: ReactNode;
    chat: ReactNode;
  }) => (
    <div data-testid="ide-layout">
      <div data-testid="slot-center">
        {header}
        <div data-testid="slot-navbar">{navBar}</div>
        <div data-testid="slot-workbench">{workbenchContent}</div>
        <div data-testid="slot-editor">{editor}</div>
      </div>
      <div data-testid="slot-chat">{chat}</div>
    </div>
  ),
}));

vi.mock("@/components/layout/horizontal-split", () => ({
  HorizontalSplit: ({ left, right }: { left: ReactNode; right: ReactNode }) => (
    <div data-testid="split">
      <div data-testid="split-left">{left}</div>
      <div data-testid="split-right">{right}</div>
    </div>
  ),
}));

vi.mock("@/lib/queries/threads", () => ({
  useThreadsQuery: () => ({ data: [], isLoading: false }),
  useDeleteThread: () => ({ mutateAsync: vi.fn() }),
  useUpdateThread: () => ({ mutate: vi.fn() }),
  threadsQueryKey: () => ["threads"],
}));

vi.mock("@/lib/api/vectora-client", () => ({
  listThreads: vi.fn().mockResolvedValue([]),
  getHistory: vi.fn().mockResolvedValue({ messages: [] }),
}));

vi.mock("../../../router", () => ({
  queryClient: {
    ensureQueryData: vi.fn(),
    prefetchQuery: vi.fn().mockResolvedValue(undefined),
    setQueryData: vi.fn(),
  },
}));

import { useSettingsStore } from "@/lib/stores/settings-store";
import { useWorkbenchStore } from "@/lib/stores/workbench-store";
import { Route } from "../$threadId";

const SessionPage = (Route as unknown as { component: () => ReactElement })
  .component;

function setMode(uiMode: "assistant" | "ide" | "kanban", chatMode = false) {
  useSettingsStore.setState({ uiMode, chatMode });
}

beforeEach(() => {
  navigateSpy.mockClear();
  // jsdom não implementa matchMedia — useIsNarrowViewport depende dele.
  // Sempre "largo": é o layout de 3 painéis que os testes afirmam.
  // jsdom não implementa EventSource — hooks de webhook o instanciam no
  // mount. Stub inerte: os testes aqui não afirmam nada sobre SSE.
  vi.stubGlobal(
    "EventSource",
    class {
      close() {}
      addEventListener() {}
      removeEventListener() {}
    },
  );
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ workspaces: [], active_id: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SessionPage — um modo por vez, nunca dois", () => {
  it("Kanban mostra o board e NENHUM chat", () => {
    // Regressão: o chat era um overlay absoluto irmão das branches de modo,
    // então continuava desenhado por cima do board depois da troca.
    setMode("kanban");
    render(<SessionPage />);

    expect(screen.getByTestId("kanban")).toBeInTheDocument();
    expect(screen.queryByTestId("chat")).not.toBeInTheDocument();
  });

  it("Assistente mostra o chat e NENHUM board", () => {
    setMode("assistant");
    render(<SessionPage />);

    expect(screen.getByTestId("chat")).toBeInTheDocument();
    expect(screen.queryByTestId("kanban")).not.toBeInTheDocument();
  });

  it("IDE mostra o chat e NENHUM board", () => {
    setMode("ide");
    render(<SessionPage />);

    expect(screen.getByTestId("chat")).toBeInTheDocument();
    expect(screen.queryByTestId("kanban")).not.toBeInTheDocument();
  });

  it("erro/borda: nunca existe mais de uma instância de chat montada", () => {
    // Duas instâncias significavam scroll/estado duplicado, efeito colateral
    // direto do overlay hoisted.
    setMode("assistant");
    render(<SessionPage />);

    expect(screen.getAllByTestId("chat")).toHaveLength(1);
  });
});

describe("SessionPage — posição do Header por modo", () => {
  // O app tem 3 colunas fixas (sidebar esquerda, centro, sidebar direita) —
  // o modo (Assistente/IDE/Kanban) só recompõe QUAIS filhos aparecem em
  // cada uma, nunca reordena as colunas em si. O Header sempre mora na
  // coluna central, nunca esparramado por cima da coluna lateral direita
  // (que muda de conteúdo por modo: workbench em Assistente, chat em IDE).

  it("IDE: Header vive na coluna central, nunca na coluna de chat (sidebar direita)", () => {
    setMode("ide");
    render(<SessionPage />);

    const header = screen.getByTestId("header");
    expect(screen.getByTestId("slot-center")).toContainElement(header);
    expect(screen.getByTestId("slot-chat")).not.toContainElement(header);
  });

  it("Assistente: Header fica dentro da coluna de chat (centro), nunca dentro do workbench (sidebar direita)", () => {
    setMode("assistant");
    render(<SessionPage />);

    const header = screen.getByTestId("header");
    expect(screen.getByTestId("shell-header-slot")).toContainElement(header);
    for (const rail of screen.getAllByRole("complementary")) {
      expect(rail).not.toContainElement(header);
    }
  });

  it("erro/borda: só existe um Header montado em qualquer modo", () => {
    for (const mode of ["assistant", "ide", "kanban"] as const) {
      cleanup();
      setMode(mode);
      render(<SessionPage />);
      expect(screen.getAllByTestId("header")).toHaveLength(1);
    }
  });
});

describe("SessionPage — sidebar de sessões", () => {
  it("fica à esquerda em Assistente/Kanban e cede lugar à workbench no IDE", () => {
    setMode("assistant");
    render(<SessionPage />);
    expect(screen.getAllByTestId("sidebar").length).toBeGreaterThan(0);

    cleanup();
    setMode("kanban");
    render(<SessionPage />);
    expect(screen.getAllByTestId("sidebar").length).toBeGreaterThan(0);

    cleanup();
    setMode("ide");
    render(<SessionPage />);
    expect(screen.queryByTestId("sidebar")).not.toBeInTheDocument();
    expect(screen.getByTestId("workbench-navbar")).toBeInTheDocument();
  });
});

describe("SessionPage — chat compacto no IDE", () => {
  it("IDE usa o chat compacto com SessionSwitcher; Assistente usa o normal", () => {
    setMode("ide");
    render(<SessionPage />);
    expect(screen.getByTestId("chat")).toHaveAttribute("data-compact", "true");
    expect(screen.getByTestId("session-switcher")).toBeInTheDocument();

    cleanup();
    setMode("assistant");
    render(<SessionPage />);
    expect(screen.getByTestId("chat")).toHaveAttribute("data-compact", "false");
    expect(screen.queryByTestId("session-switcher")).not.toBeInTheDocument();
  });
});

describe("SessionPage — workbench do Assistente estreito", () => {
  it("mantém a navegação do workbench no painel central", async () => {
    useSettingsStore.setState({ uiMode: "assistant", chatMode: false });
    useWorkbenchStore.setState({ panelOpen: { t1: true } });
    Object.defineProperty(window, "outerWidth", {
      configurable: true,
      value: 639,
    });
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 639,
    });
    vi.mocked(window.matchMedia).mockImplementation((query: string) => ({
      matches: query.includes("767px"),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));

    render(<SessionPage />);

    expect(await screen.findByTestId("workbench-navbar")).toBeInTheDocument();
    expect(await screen.findByTestId("workbench-content")).toBeInTheDocument();
    expect(screen.queryByTestId("chat")).not.toBeInTheDocument();
    Object.defineProperty(window, "outerWidth", {
      configurable: true,
      value: 1024,
    });
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1024,
    });
  });
});

describe("SessionPage — navegação compacta usa a mesma medida física", () => {
  it("mantém o gatilho quando o Electron está compacto apesar do CSS viewport largo", async () => {
    vi.stubGlobal("vectora", { windowControls: {} });
    Object.defineProperties(window, {
      outerWidth: { configurable: true, value: 800 },
      innerWidth: { configurable: true, value: 1200 },
    });
    setMode("assistant");

    render(<SessionPage />);

    await waitFor(() =>
      expect(screen.getByTestId("header-sidebar-trigger")).toBeInTheDocument(),
    );
    const trigger = screen.getByTestId("header-sidebar-trigger");
    expect(trigger).toHaveAttribute("data-compact-only", "true");
  });

  it("fecha a Sheet ao voltar para o layout largo", async () => {
    vi.stubGlobal("vectora", { windowControls: {} });
    Object.defineProperties(window, {
      outerWidth: { configurable: true, value: 800 },
      innerWidth: { configurable: true, value: 1200 },
    });
    setMode("assistant");
    render(<SessionPage />);

    await waitFor(() =>
      expect(screen.getByTestId("header-sidebar-trigger")).toBeInTheDocument(),
    );
    act(() => {
      screen.getByTestId("header-sidebar-trigger").click();
    });

    Object.defineProperty(window, "outerWidth", {
      configurable: true,
      value: 1200,
    });
    act(() => {
      window.dispatchEvent(new Event("resize"));
    });

    await waitFor(() =>
      expect(
        screen.queryByTestId("header-sidebar-trigger"),
      ).not.toBeInTheDocument(),
    );
  });
});
