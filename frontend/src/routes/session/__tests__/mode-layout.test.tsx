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
import {
  render,
  screen,
  cleanup,
  waitFor,
  act,
  fireEvent,
} from "@testing-library/react";
import { useState } from "react";
import type { ReactElement, ReactNode } from "react";

const { navigateSpy, chatProps, routeParam } = vi.hoisted(() => ({
  navigateSpy: vi.fn(),
  chatProps: vi.fn(),
  routeParam: { value: "t1" },
}));

vi.mock("@tanstack/react-router", () => ({
  // O componente lê `Route.useParams()`, então o objeto devolvido por
  // createFileRoute precisa carregá-lo — não basta repassar as opções.
  createFileRoute: () => (opts: Record<string, unknown>) => ({
    ...opts,
    useParams: () => ({ threadId: routeParam.value }),
  }),
  useNavigate: () => navigateSpy,
}));

vi.mock("@/components/chat/chat-interface", () => ({
  ChatInterface: ({
    compact,
    isNewThread,
    threadId,
  }: {
    compact?: boolean;
    isNewThread?: boolean;
    threadId: string;
  }) => {
    chatProps({ compact, isNewThread });
    const [response, setResponse] = useState("");
    const [isStreaming, setIsStreaming] = useState(false);
    const [requestCount, setRequestCount] = useState(0);
    return (
      <div
        data-testid="chat"
        data-compact={String(!!compact)}
        data-thread-id={threadId}
      >
        <output data-testid="stream-response">{response}</output>
        <output data-testid="stream-request-count">{requestCount}</output>
        <button
          data-testid="stream-send"
          onClick={() => {
            setRequestCount((count) => count + 1);
            setResponse("Resposta parcial");
            setIsStreaming(true);
          }}
        >
          Enviar
        </button>
        {isStreaming && (
          <>
            <button
              data-testid="stream-stop"
              onClick={() => setIsStreaming(false)}
            >
              Parar
            </button>
            <button
              data-testid="stream-next-token"
              onClick={() => setResponse("Resposta parcial atualizada")}
            >
              Próximo token
            </button>
            <button
              data-testid="stream-finish"
              onClick={() => {
                setResponse("Resposta completa");
                setIsStreaming(false);
              }}
            >
              Finalizar resposta
            </button>
          </>
        )}
      </div>
    );
  },
}));
vi.mock("@/components/kanban/kanban-board", () => ({
  KanbanBoard: () => <div data-testid="kanban" />,
}));
vi.mock("@/components/header/header", () => ({
  Header: ({
    compactNavigation,
  }: {
    compactNavigation?: {
      workbenchOpen?: boolean;
      onToggleWorkbench?: () => void;
    };
  }) => (
    <div
      data-testid="header"
      data-compact-navigation={String(!!compactNavigation)}
    >
      {compactNavigation?.onToggleWorkbench && (
        <button
          type="button"
          data-testid="compact-toggle-workbench"
          aria-pressed={compactNavigation.workbenchOpen}
          onClick={compactNavigation.onToggleWorkbench}
        >
          Workbench
        </button>
      )}
    </div>
  ),
}));
vi.mock("@/components/header/mode-switcher", () => ({
  ModeSwitch: () => <div data-testid="mode-switch" />,
}));
vi.mock("@/components/sidebar/sidebar", () => ({
  Sidebar: ({ onNewChat }: { onNewChat: () => void }) => (
    <div data-testid="sidebar">
      <button data-testid="sidebar-new-session" onClick={onNewChat} />
    </div>
  ),
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
  NewChatDialog: ({ open }: { open: boolean }) =>
    open ? <div role="dialog" data-testid="new-chat-dialog" /> : null,
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
    layoutState,
    workbenchOpen,
    onOpenWorkbench,
  }: {
    header: ReactNode;
    navBar: ReactNode;
    workbenchContent: ReactNode;
    editor: ReactNode;
    chat: ReactNode;
    layoutState?: "wide" | "mobile";
    workbenchOpen?: boolean;
    onOpenWorkbench?: () => void;
  }) => (
    <div data-testid="ide-layout">
      {layoutState === "mobile" && (
        <button
          type="button"
          data-testid="ide-mobile-tab-workbench"
          onClick={onOpenWorkbench}
        >
          {workbenchOpen ? "Workbench aberta" : "Abrir Workbench"}
        </button>
      )}
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
  useThreadsPaging: () => ({
    hasMore: false,
    remaining: 0,
    isLoadingMore: false,
    loadMore: async () => {},
  }),
  useDeleteThread: () => ({ mutateAsync: vi.fn() }),
  useUpdateThread: () => ({ mutate: vi.fn() }),
  threadsQueryKey: () => ["threads"],
}));

vi.mock("@/lib/api/vectora-client", () => ({
  listThreads: vi.fn().mockResolvedValue([]),
  getHistory: vi.fn().mockResolvedValue({ messages: [] }),
  // SessionPage marks an existing route as read on mount. Keep this mocked
  // as a resolved idempotent RPC so layout tests exercise rendering rather
  // than failing before the mode composition is committed.
  markThreadRead: vi.fn().mockResolvedValue({}),
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
  chatProps.mockClear();
  routeParam.value = "t1";
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
  it("marca a rota de nova conversa antes dos efeitos de registro", () => {
    routeParam.value = "new";
    setMode("assistant");

    render(<SessionPage />);

    expect(chatProps).toHaveBeenCalledWith(
      expect.objectContaining({ isNewThread: true }),
    );
  });

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

  it("abre o diálogo de nova sessão pelo sidebar do modo Kanban", () => {
    setMode("kanban");
    render(<SessionPage />);

    fireEvent.click(screen.getAllByTestId("sidebar-new-session")[0]!);

    expect(screen.getByTestId("new-chat-dialog")).toBeInTheDocument();
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

  it("preserva o stream ao abrir e fechar a Workbench no Assistente compacto", async () => {
    setMode("assistant");
    useWorkbenchStore.setState({ panelOpen: { t1: false } });
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 818,
    });

    render(<SessionPage />);

    const toggleWorkbench = await screen.findByTestId(
      "compact-toggle-workbench",
    );
    fireEvent.click(screen.getByTestId("stream-send"));

    expect(screen.getByTestId("stream-response")).toHaveTextContent(
      "Resposta parcial",
    );
    expect(screen.getByTestId("stream-stop")).toBeInTheDocument();
    expect(screen.getByTestId("stream-request-count")).toHaveTextContent("1");

    fireEvent.click(toggleWorkbench);
    expect(await screen.findByTestId("workbench-content")).toBeInTheDocument();
    expect(
      screen.getByTestId("chat").parentElement?.parentElement?.parentElement,
    ).toHaveStyle({ opacity: "0", pointerEvents: "none" });
    expect(
      screen.getByTestId("chat").parentElement?.parentElement?.parentElement,
    ).toHaveAttribute("inert");
    expect(screen.getByTestId("stream-response")).toHaveTextContent(
      "Resposta parcial",
    );
    fireEvent.click(screen.getByTestId("stream-next-token"));
    expect(screen.getByTestId("stream-response")).toHaveTextContent(
      "Resposta parcial atualizada",
    );

    fireEvent.click(toggleWorkbench);
    await waitFor(() =>
      expect(
        screen.getByTestId("chat").parentElement?.parentElement?.parentElement,
      ).toHaveStyle({ opacity: "1", pointerEvents: "auto" }),
    );
    expect(screen.getByTestId("stream-response")).toHaveTextContent(
      "Resposta parcial atualizada",
    );
    expect(screen.getByTestId("stream-stop")).toBeInTheDocument();
    expect(screen.getByTestId("stream-request-count")).toHaveTextContent("1");

    fireEvent.click(screen.getByTestId("stream-finish"));
    expect(screen.getByTestId("stream-response")).toHaveTextContent(
      "Resposta completa",
    );
    expect(screen.queryByTestId("stream-stop")).not.toBeInTheDocument();
    expect(screen.getByTestId("stream-request-count")).toHaveTextContent("1");
  });

  it("preserva a instância do chat ao cruzar o breakpoint durante o stream", async () => {
    setMode("assistant");
    useWorkbenchStore.setState({ panelOpen: { t1: false } });
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 818,
    });
    Object.defineProperty(window, "visualViewport", {
      configurable: true,
      value: undefined,
    });

    render(<SessionPage />);
    const chat = await screen.findByTestId("chat");
    const chatLayout = chat.parentElement?.parentElement?.parentElement;
    expect(chatLayout).toHaveClass("absolute");
    fireEvent.click(screen.getByTestId("stream-send"));

    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1024,
    });
    fireEvent(window, new Event("resize"));
    await waitFor(() =>
      expect(screen.getByTestId("header")).toHaveAttribute(
        "data-compact-navigation",
        "false",
      ),
    );
    expect(chatLayout).toHaveClass("absolute");
    expect(screen.getByTestId("chat")).toBe(chat);
    expect(screen.getByTestId("chat")).toHaveAttribute("data-compact", "false");
    expect(screen.getByTestId("stream-response")).toHaveTextContent(
      "Resposta parcial",
    );
    expect(screen.getByTestId("stream-stop")).toBeInTheDocument();

    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 818,
    });
    fireEvent(window, new Event("resize"));
    await waitFor(() =>
      expect(screen.getByTestId("header")).toHaveAttribute(
        "data-compact-navigation",
        "true",
      ),
    );
    expect(chatLayout).toHaveClass("absolute");
    expect(screen.getByTestId("chat")).toBe(chat);
    expect(screen.getByTestId("stream-request-count")).toHaveTextContent("1");
  });

  it("isola o stream ao trocar de sessão com a Workbench aberta", async () => {
    setMode("assistant");
    useWorkbenchStore.setState({ panelOpen: { t1: false, t2: true } });
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 818,
    });

    const { rerender } = render(<SessionPage />);
    fireEvent.click(await screen.findByTestId("stream-send"));
    fireEvent.click(screen.getByTestId("compact-toggle-workbench"));
    expect(await screen.findByTestId("workbench-content")).toBeInTheDocument();
    expect(screen.getByTestId("stream-stop")).toBeInTheDocument();

    routeParam.value = "t2";
    rerender(<SessionPage />);

    await waitFor(() =>
      expect(screen.getByTestId("chat")).toHaveAttribute(
        "data-thread-id",
        "t2",
      ),
    );
    expect(screen.queryByTestId("stream-stop")).not.toBeInTheDocument();
    expect(screen.getByTestId("stream-response")).toBeEmptyDOMElement();
    expect(
      screen.getByTestId("chat").parentElement?.parentElement?.parentElement,
    ).toHaveStyle({ opacity: "0", pointerEvents: "none" });
  });

  it("mantém o chat central ao abrir a Workbench no layout largo", async () => {
    setMode("assistant");
    useWorkbenchStore.setState({ panelOpen: { t1: true } });
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1024,
    });

    render(<SessionPage />);

    expect(await screen.findByTestId("workbench-content")).toBeInTheDocument();
    expect(await screen.findByTestId("chat")).toBeInTheDocument();
    expect(
      screen.getByTestId("chat").parentElement?.parentElement?.parentElement,
    ).toHaveStyle({ opacity: "1", pointerEvents: "auto" });
  });
});

describe("SessionPage — workbench do IDE compacto", () => {
  it("reabre a workbench fechada ao selecionar a aba em uma janela de 818px", async () => {
    setMode("ide");
    useWorkbenchStore.setState({ panelOpen: { t1: false } });
    Object.defineProperty(window, "outerWidth", {
      configurable: true,
      value: 818,
    });
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 818,
    });

    render(<SessionPage />);

    const workbenchTab = await screen.findByTestId("ide-mobile-tab-workbench");
    expect(workbenchTab).toHaveTextContent("Abrir Workbench");

    act(() => workbenchTab.click());

    await waitFor(() =>
      expect(useWorkbenchStore.getState().isOpen("t1")).toBe(true),
    );
    expect(screen.getByTestId("ide-mobile-tab-workbench")).toHaveTextContent(
      "Workbench aberta",
    );
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
    expect(
      (await screen.findByTestId("chat")).parentElement?.parentElement
        ?.parentElement,
    ).toHaveStyle({ opacity: "0", pointerEvents: "none" });
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
