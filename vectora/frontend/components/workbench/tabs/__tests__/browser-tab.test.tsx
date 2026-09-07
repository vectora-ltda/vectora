// @vitest-environment jsdom
/**
 * BrowserTab — navegação livre (barra de URL sempre ativa, histórico
 * voltar/avançar) e gerenciamento de servidores de dev do workspace
 * (iniciar/parar/logar) como atalhos dentro do mesmo painel. O iframe só
 * navega quando o backend confirma a porta aberta, nunca só porque o
 * processo existe. Cobre também URL externa sem servidor configurado e
 * navegação back/forward.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  waitFor,
  fireEvent,
  act,
} from "@testing-library/react";

import { BrowserTab, clearBrowserSessionCache } from "../browser-tab";
import {
  disposeBrowserWorkspace,
  disposeBrowserSession,
  setBrowserSession,
} from "@/lib/browser-session-store";

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

const makeBrowserTestTab = (viewId: number | null) => ({
  id: `tab-${viewId}`,
  title: "",
  history: [],
  historyIndex: -1,
  iframeKey: 0,
  viewId,
  desktopUrl: "",
  canGoBack: false,
  canGoForward: false,
});

const workspaceState = vi.hoisted(() => ({ id: "ws1" }));

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (
    sel: (s: { getActive: () => { id: string } | undefined }) => unknown,
  ) => sel({ getActive: () => workspaceState }),
}));

vi.mock("@/lib/stores/chat-input-store", () => ({
  useChatInputStore: { getState: () => ({ pushDraft: vi.fn() }) },
}));

afterEach(() => {
  cleanup();
  workspaceState.id = "ws1";
  clearBrowserSessionCache();
});

// jsdom não implementa ResizeObserver — só o caminho desktop (efeito de
// bounds da WebContentsView) o usa; um stub no-op basta pros testes.
class StubResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).ResizeObserver ??= StubResizeObserver;

const LAUNCH = {
  version: "0.0.1",
  configurations: [
    {
      name: "web",
      runtimeExecutable: "npm",
      runtimeArgs: ["run", "dev"],
      port: 3001,
    },
  ],
};

function mockFetch({
  configurations = LAUNCH.configurations,
  startRunning = false,
  unmanagedRunning = false,
  logLines,
}: {
  configurations?: typeof LAUNCH.configurations;
  startRunning?: boolean;
  // Porta aberta (algo escutando) mas sem PID rastreado por este config —
  // cenário de dois configs colidindo na mesma porta ("running externo").
  unmanagedRunning?: boolean;
  logLines?: string[];
} = {}) {
  global.fetch = vi
    .fn()
    .mockImplementation((url: string, init?: RequestInit) => {
      if (
        url.endsWith("/browser/launch") &&
        (!init || init.method === undefined)
      ) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ version: "0.0.1", configurations }),
        } as Response);
      }
      if (url.endsWith("/browser/status")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            servers: configurations.map((c) => ({
              name: c.name,
              port: c.port,
              running: startRunning || unmanagedRunning,
              pid: startRunning ? 1 : null,
            })),
          }),
        } as Response);
      }
      if (url.endsWith("/browser/start")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ status: "ok" }),
        } as Response);
      }
      if (url.includes("/browser/logs")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ lines: logLines ?? [] }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });
}

describe("BrowserTab — servidores de dev (paridade com o antigo Preview)", () => {
  it("clicar em iniciar com backend ainda compilando não navega o iframe (nenhuma porta aberta ainda)", async () => {
    mockFetch({ startRunning: false });
    render(<BrowserTab threadId="t1" />);

    const startBtn = await screen.findByTitle("workbench_browser_start");
    fireEvent.click(startBtn);

    await waitFor(() => {
      expect(document.querySelector("iframe")).toBeNull();
    });
  });

  it("com a porta já aberta (status inicial), clicar em 'abrir servidor' navega o iframe pra localhost:<port>", async () => {
    mockFetch({ startRunning: true });
    render(<BrowserTab threadId="t1" />);

    const openBtn = await screen.findByTitle("workbench_browser_open_server");
    fireEvent.click(openBtn);

    const iframe = await screen.findByTitle("Browser");
    expect(iframe.getAttribute("src")).toBe("http://localhost:3001");
  });

  it("botão de console abre o painel e mostra as linhas de log do servidor", async () => {
    mockFetch({ startRunning: false, logLines: ["compiling...", "ready"] });
    render(<BrowserTab threadId="t1" />);

    const consoleBtn = await screen.findByTitle("workbench_browser_console");
    fireEvent.click(consoleBtn);

    await waitFor(() => {
      expect(screen.getByText("compiling...")).toBeTruthy();
      expect(screen.getByText("ready")).toBeTruthy();
    });
  });

  it("servidor nunca iniciado mostra estado vazio específico do console, não erro", async () => {
    mockFetch({ startRunning: false, logLines: [] });
    render(<BrowserTab threadId="t1" />);

    const consoleBtn = await screen.findByTitle("workbench_browser_console");
    fireEvent.click(consoleBtn);

    await waitFor(() => {
      expect(screen.getByText("workbench_browser_console_empty")).toBeTruthy();
    });
  });

  it("clicar no cabeçalho 'Servidores' recolhe a lista; clicar de novo expande", async () => {
    mockFetch({ startRunning: false });
    render(<BrowserTab threadId="t1" />);

    await screen.findByTitle("workbench_browser_start");
    expect(screen.getByText("web")).toBeTruthy();

    fireEvent.click(screen.getByTitle("workbench_browser_servers_collapse"));
    expect(screen.queryByText("web")).toBeNull();

    fireEvent.click(screen.getByTitle("workbench_browser_servers_expand"));
    expect(screen.getByText("web")).toBeTruthy();
  });

  it("recolher os servidores não fecha o formulário de adicionar manualmente por engano (botões independentes)", async () => {
    mockFetch({ startRunning: false });
    render(<BrowserTab threadId="t1" />);

    fireEvent.click(await screen.findByTitle("workbench_browser_manual_add"));
    expect(
      screen.getByPlaceholderText("workbench_browser_field_name"),
    ).toBeTruthy();

    fireEvent.click(screen.getByTitle("workbench_browser_servers_collapse"));
    // Recolhido: o formulário de adicionar servidor some junto com a lista,
    // mas o botão "+" continua funcionando e independente do de colapsar.
    expect(
      screen.queryByPlaceholderText("workbench_browser_field_name"),
    ).toBeNull();

    fireEvent.click(screen.getByTitle("workbench_browser_servers_expand"));
    expect(
      screen.getByPlaceholderText("workbench_browser_field_name"),
    ).toBeTruthy();
  });
});

describe("BrowserTab — navegação livre (sem depender de servidor configurado)", () => {
  it("digitar uma URL externa e pressionar Enter navega o iframe, mesmo sem nenhum servidor configurado", async () => {
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);

    const urlBar = await screen.findByTestId("browser-url-bar");
    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "google.com" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });

    const iframe = await screen.findByTitle("Browser");
    expect(iframe.getAttribute("src")).toBe("https://google.com");
  });

  it("sem nenhuma URL navegada e sem servidores configurados, mostra o estado vazio com onboarding (pedir ao agente / adicionar manualmente)", async () => {
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);

    await waitFor(() => {
      expect(screen.getByText("workbench_browser_empty_title")).toBeTruthy();
    });
    expect(screen.getByText("workbench_browser_ask_agent")).toBeTruthy();
    expect(document.querySelector("iframe")).toBeNull();
  });
});

describe("BrowserTab — histórico voltar/avançar", () => {
  it("voltar/avançar navegam entre URLs visitadas; avançar fica desabilitado até haver histórico à frente", async () => {
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);

    const urlBar = await screen.findByTestId("browser-url-bar");
    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "example.com" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });
    await screen.findByTitle("Browser");

    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "example.org" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });
    await waitFor(() => {
      expect(document.querySelector("iframe")?.getAttribute("src")).toBe(
        "https://example.org",
      );
    });

    const backBtn = screen.getByTitle("workbench_browser_back");
    fireEvent.click(backBtn);
    await waitFor(() => {
      expect(document.querySelector("iframe")?.getAttribute("src")).toBe(
        "https://example.com",
      );
    });

    const forwardBtn = screen.getByTitle("workbench_browser_forward");
    fireEvent.click(forwardBtn);
    await waitFor(() => {
      expect(document.querySelector("iframe")?.getAttribute("src")).toBe(
        "https://example.org",
      );
    });
  });

  it("navegar pra uma nova URL depois de voltar descarta o ramo antigo do histórico (avançar fica desabilitado)", async () => {
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);

    const urlBar = await screen.findByTestId("browser-url-bar");
    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "example.com" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });
    await screen.findByTitle("Browser");

    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "example.org" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });
    await waitFor(() => {
      expect(document.querySelector("iframe")?.getAttribute("src")).toBe(
        "https://example.org",
      );
    });

    fireEvent.click(screen.getByTitle("workbench_browser_back"));
    await waitFor(() => {
      expect(document.querySelector("iframe")?.getAttribute("src")).toBe(
        "https://example.com",
      );
    });

    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "example.net" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });
    await waitFor(() => {
      expect(document.querySelector("iframe")?.getAttribute("src")).toBe(
        "https://example.net",
      );
    });

    const forwardBtn = screen.getByTitle("workbench_browser_forward");
    expect(forwardBtn).toBeDisabled();
  });
});

describe("BrowserTab — auto-navegação quando um servidor sobe", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("servidor que passa de parado pra rodando entre polls navega sozinho, sem clique — mesmo caminho da tool do agente", async () => {
    let running = false;
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith("/browser/launch")) {
        return Promise.resolve({
          ok: true,
          json: async () => LAUNCH,
        } as Response);
      }
      if (url.endsWith("/browser/status")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            servers: [
              { name: "web", port: 3001, running, pid: running ? 1 : null },
            ],
          }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });

    render(<BrowserTab threadId="t1" />);

    // Primeiro poll: parado — não é uma transição, não navega.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(document.querySelector("iframe")).toBeNull();

    // O servidor sobe "por fora" (ex.: tool browser_start do agente) — o
    // próximo poll (3s) já reflete running:true.
    running = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    const iframe = document.querySelector("iframe");
    expect(iframe?.getAttribute("src")).toBe("http://localhost:3001");
  });

  it("servidor já rodando desde o primeiro poll não navega sozinho (só a transição conta, não o estado inicial)", async () => {
    mockFetch({ startRunning: true });
    render(<BrowserTab threadId="t1" />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(document.querySelector("iframe")).toBeNull();
  });
});

describe("BrowserTab — console inline, não popup", () => {
  it("painel do console renderiza dentro do container da própria aba, não num portal/dialog em document.body", async () => {
    mockFetch({ startRunning: false, logLines: ["ready"] });
    const { container } = render(<BrowserTab threadId="t1" />);

    const consoleBtn = await screen.findByTitle("workbench_browser_console");
    fireEvent.click(consoleBtn);

    await waitFor(() => {
      expect(container.textContent).toContain("ready");
    });
    // Nunca deve existir role="dialog" (Radix Sheet/Dialog) — é um painel
    // inline, não um portal sobrepondo a janela.
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("botão de fechar o console some o painel sem afetar o iframe carregado", async () => {
    mockFetch({ startRunning: true, logLines: ["ready"] });
    render(<BrowserTab threadId="t1" />);

    const openBtn = await screen.findByTitle("workbench_browser_open_server");
    fireEvent.click(openBtn);
    await screen.findByTitle("Browser");

    fireEvent.click(await screen.findByTitle("workbench_browser_console"));
    await waitFor(() => expect(screen.getByText("ready")).toBeTruthy());

    fireEvent.click(screen.getByTitle("workbench_browser_console_close"));
    await waitFor(() => {
      expect(screen.queryByText("ready")).toBeNull();
    });
    expect(screen.getByTitle("Browser")).toBeTruthy();
  });
});

describe("BrowserTab — sandbox do iframe", () => {
  it("servidor de dev do próprio workspace ganha allow-same-origin (CSS do Next.js precisa disso)", async () => {
    mockFetch({ startRunning: true });
    render(<BrowserTab threadId="t1" />);

    const openBtn = await screen.findByTitle("workbench_browser_open_server");
    fireEvent.click(openBtn);

    const iframe = await screen.findByTitle("Browser");
    expect(iframe.getAttribute("sandbox")).toContain("allow-same-origin");
  });

  it("URL externa navegada livremente nunca ganha allow-same-origin, mesmo depois de já ter aberto um servidor do workspace", async () => {
    mockFetch({ startRunning: true });
    render(<BrowserTab threadId="t1" />);

    const urlBar = await screen.findByTestId("browser-url-bar");
    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "example.com" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });

    const iframe = await screen.findByTitle("Browser");
    expect(iframe.getAttribute("src")).toBe("https://example.com");
    expect(iframe.getAttribute("sandbox")).not.toContain("allow-same-origin");
  });
});

describe("BrowserTab — caminho desktop (WebContentsView real via window.vectora.browserView)", () => {
  type EventHandler = (
    viewId: number,
    event: {
      type: string;
      url?: string;
      canGoBack?: boolean;
      canGoForward?: boolean;
      isLoading?: boolean;
      errorDescription?: string;
    },
  ) => void;

  function mockBrowserView() {
    const calls = {
      createView: vi.fn(),
      destroyView: vi.fn(),
      navigate: vi.fn(async (_viewId: number, url: string) => {
        if (url === "https://falha.invalid") {
          return { ok: false, error: "esquema não permitido: ftp://x" };
        }
        return { ok: true };
      }),
      goBack: vi.fn(),
      goForward: vi.fn(),
      reload: vi.fn(),
      setBounds: vi.fn(),
      setVisible: vi.fn(),
    };
    let handler: EventHandler | null = null;
    const bridge = {
      ...calls,
      createView: vi
        .fn<() => Promise<number>>()
        .mockResolvedValueOnce(1)
        .mockResolvedValue(2),
      onEvent: vi.fn((h: EventHandler) => {
        handler = h;
        return () => {
          handler = null;
        };
      }),
      emitEvent: (viewId: number, event: Parameters<EventHandler>[1]) =>
        handler?.(viewId, event),
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).vectora = { browserView: bridge };
    return bridge;
  }

  afterEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (window as any).vectora;
  });

  it("no desktop, digitar uma URL chama a bridge e renderiza a área da WebContentsView, nunca um <iframe>", async () => {
    const bridge = mockBrowserView();
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);

    await waitFor(() => expect(bridge.onEvent).toHaveBeenCalled());

    const urlBar = await screen.findByTestId("browser-url-bar");
    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "example.com" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });

    await waitFor(() => {
      expect(bridge.navigate).toHaveBeenCalledWith(1, "https://example.com");
    });
    expect(document.querySelector("iframe")).toBeNull();
    expect(
      screen.getByTestId("browser-webcontentsview-container"),
    ).toBeTruthy();
  });

  it("trocar de workspace sem sessão cria a WebContentsView nativa e navega nela", async () => {
    const bridge = mockBrowserView();
    mockFetch({ configurations: [] });
    const view = render(<BrowserTab threadId="electron-workspace-switch" />);
    await waitFor(() => expect(bridge.createView).toHaveBeenCalledTimes(1));

    workspaceState.id = "ws2";
    view.rerender(<BrowserTab threadId="electron-workspace-switch" />);
    await waitFor(() => expect(bridge.createView).toHaveBeenCalledTimes(2));

    const urlBar = await screen.findByTestId("browser-url-bar");
    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "ws2.example" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });
    await waitFor(() =>
      expect(bridge.navigate).toHaveBeenCalledWith(2, "https://ws2.example"),
    );
  });

  it("evento navigated do main atualiza a barra de URL e can-go-back/forward — nunca escritos manualmente", async () => {
    const bridge = mockBrowserView();
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);
    await waitFor(() => expect(bridge.onEvent).toHaveBeenCalled());

    act(() => {
      bridge.emitEvent(1, {
        type: "navigated",
        url: "https://example.com/pagina",
        canGoBack: true,
        canGoForward: false,
      });
    });

    const urlBar = await screen.findByTestId("browser-url-bar");
    await waitFor(() => {
      expect(urlBar).toHaveValue("https://example.com/pagina");
    });
    expect(screen.getByTitle("workbench_browser_back")).not.toBeDisabled();
    expect(screen.getByTitle("workbench_browser_forward")).toBeDisabled();
  });

  it("voltar/avançar/recarregar chamam a bridge, nunca a lógica de histórico do iframe", async () => {
    const bridge = mockBrowserView();
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);
    await waitFor(() => expect(bridge.onEvent).toHaveBeenCalled());

    act(() => {
      bridge.emitEvent(1, {
        type: "navigated",
        url: "https://example.com",
        canGoBack: true,
        canGoForward: true,
      });
    });

    fireEvent.click(await screen.findByTitle("workbench_browser_back"));
    fireEvent.click(screen.getByTitle("workbench_browser_forward"));
    fireEvent.click(screen.getByTitle("workbench_files_refresh"));

    expect(bridge.goBack).toHaveBeenCalledWith(1);
    expect(bridge.goForward).toHaveBeenCalledWith(1);
    expect(bridge.reload).toHaveBeenCalledWith(1);
  });

  it("falha ao navegar (esquema não permitido) mostra a mensagem de erro; navegação seguinte bem-sucedida a limpa", async () => {
    const bridge = mockBrowserView();
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);
    await waitFor(() => expect(bridge.onEvent).toHaveBeenCalled());

    const urlBar = await screen.findByTestId("browser-url-bar");
    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "https://falha.invalid" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });

    await waitFor(() => {
      expect(screen.getByText("esquema não permitido: ftp://x")).toBeTruthy();
    });

    act(() => {
      bridge.emitEvent(1, {
        type: "navigated",
        url: "https://outra.com",
        canGoBack: false,
        canGoForward: false,
      });
    });
    await waitFor(() => {
      expect(screen.queryByText("esquema não permitido: ftp://x")).toBeNull();
    });
  });

  it("oculta as views nativas quando o painel está fechado e as mostra ao reabrir", async () => {
    const bridge = mockBrowserView();
    mockFetch({ configurations: [] });
    const { rerender } = render(<BrowserTab threadId="t1" visible={false} />);
    await waitFor(() => expect(bridge.createView).toHaveBeenCalled());
    await waitFor(() =>
      expect(bridge.setVisible).toHaveBeenCalledWith(1, false),
    );

    rerender(<BrowserTab threadId="t1" visible />);
    await waitFor(() =>
      expect(bridge.setVisible).toHaveBeenCalledWith(1, true),
    );
  });

  it("desmontar o painel apenas oculta a view para preservá-la ao reabrir", async () => {
    const bridge = mockBrowserView();
    mockFetch({ configurations: [] });
    const { unmount } = render(<BrowserTab threadId="t1" />);
    await waitFor(() => expect(bridge.onEvent).toHaveBeenCalled());

    unmount();
    expect(bridge.setVisible).toHaveBeenCalledWith(1, false);
    expect(bridge.destroyView).not.toHaveBeenCalled();
  });

  it("descarta todas as views ao excluir a sessão explicitamente", () => {
    const bridge = mockBrowserView();
    setBrowserSession("ws1:thread-to-delete", {
      activeTabId: "tab-1",
      tabs: [
        {
          id: "tab-1",
          title: "",
          history: [],
          historyIndex: -1,
          iframeKey: 0,
          viewId: 11,
          desktopUrl: "https://one.example",
          canGoBack: false,
          canGoForward: false,
        },
        {
          id: "tab-2",
          title: "",
          history: [],
          historyIndex: -1,
          iframeKey: 0,
          viewId: 12,
          desktopUrl: "https://two.example",
          canGoBack: false,
          canGoForward: false,
        },
      ],
    });

    disposeBrowserSession("ws1:thread-to-delete");

    expect(bridge.destroyView).toHaveBeenCalledWith(11);
    expect(bridge.destroyView).toHaveBeenCalledWith(12);
  });

  it("descarta as sessões de todas as threads quando um workspace é removido", () => {
    const bridge = mockBrowserView();
    setBrowserSession("ws-deleted:t1", {
      activeTabId: "tab-21",
      tabs: [makeBrowserTestTab(21)],
    });
    setBrowserSession("ws-deleted:t2", {
      activeTabId: "tab-22",
      tabs: [makeBrowserTestTab(22)],
    });

    disposeBrowserWorkspace("ws-deleted");

    expect(bridge.destroyView).toHaveBeenCalledWith(21);
    expect(bridge.destroyView).toHaveBeenCalledWith(22);
  });

  it("fecha uma aba antes de createView resolver sem navegar nem deixar view órfã", async () => {
    const resolvers: Array<(viewId: number) => void> = [];
    const bridge = mockBrowserView();
    bridge.createView = vi.fn(
      () => new Promise<number>((resolve) => resolvers.push(resolve)),
    );
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);
    await waitFor(() => expect(bridge.createView).toHaveBeenCalled());
    const close = await screen.findByTitle("workbench_browser_close_tab");
    fireEvent.click(close);

    await act(async () => resolvers[0](99));
    expect(bridge.destroyView).toHaveBeenCalledWith(99);
    expect(bridge.navigate).not.toHaveBeenCalled();
  });

  it("ignora uma view criada depois que a sessão foi descartada", async () => {
    const resolvers: Array<(viewId: number) => void> = [];
    const bridge = mockBrowserView();
    bridge.createView = vi.fn(
      () => new Promise<number>((resolve) => resolvers.push(resolve)),
    );
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="generation-thread" />);
    await waitFor(() => expect(bridge.createView).toHaveBeenCalled());

    disposeBrowserSession("ws1:generation-thread");
    await act(async () => resolvers[0](101));

    expect(bridge.destroyView).toHaveBeenCalledWith(101);
    expect(bridge.navigate).not.toHaveBeenCalled();
  });
});

describe("BrowserTab — restauração por sessão", () => {
  it("trocar de workspace restaura a sessão do destino sem reutilizar abas da origem", async () => {
    setBrowserSession("ws1:workspace-switch", {
      activeTabId: "tab-ws1",
      tabs: [
        {
          ...makeBrowserTestTab(null),
          id: "tab-ws1",
          history: ["https://ws1.example"],
          historyIndex: 0,
        },
      ],
    });
    setBrowserSession("ws2:workspace-switch", {
      activeTabId: "tab-ws2",
      tabs: [
        {
          ...makeBrowserTestTab(null),
          id: "tab-ws2",
          history: ["https://ws2.example"],
          historyIndex: 0,
        },
      ],
    });
    mockFetch({ configurations: [] });

    const view = render(<BrowserTab threadId="workspace-switch" />);
    await waitFor(() =>
      expect(screen.getByTestId("browser-url-bar")).toHaveValue(
        "https://ws1.example",
      ),
    );

    workspaceState.id = "ws2";
    view.rerender(<BrowserTab threadId="workspace-switch" />);
    await waitFor(() =>
      expect(screen.getByTestId("browser-url-bar")).toHaveValue(
        "https://ws2.example",
      ),
    );
  });

  it("restaura a URL da mesma thread depois de ocultar/remontar o painel", async () => {
    mockFetch({ configurations: [] });
    const first = render(<BrowserTab threadId="restore-thread" />);
    const urlBar = await screen.findByTestId("browser-url-bar");
    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "example.com" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });
    await waitFor(() =>
      expect(document.querySelector("iframe")).toHaveAttribute(
        "src",
        "https://example.com",
      ),
    );

    first.unmount();
    render(<BrowserTab threadId="restore-thread" />);
    await waitFor(() =>
      expect(screen.getByTestId("browser-url-bar")).toHaveValue(
        "https://example.com",
      ),
    );
  });
});

describe("BrowserTab — servidor rodando mas não gerenciado pelo Vectora (porta colidindo entre dois configs)", () => {
  it("running=true com pid=null desabilita o botão de parar e mostra tooltip explicativo, sem quebrar o indicador visual", async () => {
    mockFetch({ unmanagedRunning: true });
    render(<BrowserTab threadId="t1" />);

    // O indicador continua honesto (a porta está mesmo aberta), mas o
    // botão de ação não oferece "parar" — não há PID rastreado pra matar.
    const toggleBtn = await screen.findByTitle(
      "workbench_browser_running_external",
    );
    expect(toggleBtn).toBeDisabled();
    expect(screen.queryByTitle("workbench_browser_stop")).toBeNull();
  });

  it("running=true com pid definido continua oferecendo o botão de parar normalmente (regressão)", async () => {
    mockFetch({ startRunning: true });
    render(<BrowserTab threadId="t1" />);

    const stopBtn = await screen.findByTitle("workbench_browser_stop");
    expect(stopBtn).not.toBeDisabled();
  });
});

describe("BrowserTab — múltiplas abas", () => {
  it("clicar em '+' cria uma aba nova em branco e a foca (nenhuma URL carregada ainda)", async () => {
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);

    fireEvent.click(await screen.findByTitle("workbench_browser_new_tab"));

    const items = screen.getAllByTestId("browser-tab-strip-item");
    expect(items).toHaveLength(2);
    // A aba nova (em branco) virou ativa — nenhum iframe carregado ainda.
    expect(document.querySelector("iframe")).toBeNull();
  });

  it("cada aba mantém seu próprio histórico — navegar na aba ativa não afeta as demais", async () => {
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);

    fireEvent.click(await screen.findByTitle("workbench_browser_new_tab"));
    const urlBar = await screen.findByTestId("browser-url-bar");
    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "example.com" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });
    await screen.findByTitle("Browser");

    const items = screen.getAllByTestId("browser-tab-strip-item");
    // Volta pra primeira aba (a original, ainda em branco).
    fireEvent.click(items[0]);
    expect(document.querySelector("iframe")).toBeNull();
  });

  it("fechar uma aba não-ativa não muda a aba ativa nem afeta seu conteúdo", async () => {
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);

    const urlBar = await screen.findByTestId("browser-url-bar");
    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "example.com" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });
    await screen.findByTitle("Browser");

    fireEvent.click(await screen.findByTitle("workbench_browser_new_tab"));
    expect(document.querySelector("iframe")).toBeNull();

    const items = screen.getAllByTestId("browser-tab-strip-item");
    const closeBtn = items[0].querySelector(
      `[title="workbench_browser_close_tab"]`,
    ) as HTMLElement;
    fireEvent.click(closeBtn);

    // A aba nova (ativa) continua ativa e em branco.
    expect(document.querySelector("iframe")).toBeNull();
    expect(screen.getAllByTestId("browser-tab-strip-item")).toHaveLength(1);
  });

  it("fechar a aba ativa foca a aba vizinha", async () => {
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);

    const urlBar = await screen.findByTestId("browser-url-bar");
    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "example.com" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });
    await screen.findByTitle("Browser");

    fireEvent.click(await screen.findByTitle("workbench_browser_new_tab"));
    expect(document.querySelector("iframe")).toBeNull();

    // Fecha a aba ativa (a segunda, em branco) — deve voltar pra primeira,
    // que tem example.com carregado.
    const items = screen.getAllByTestId("browser-tab-strip-item");
    const closeBtn = items[1].querySelector(
      `[title="workbench_browser_close_tab"]`,
    ) as HTMLElement;
    fireEvent.click(closeBtn);

    await waitFor(() => {
      expect(document.querySelector("iframe")?.getAttribute("src")).toBe(
        "https://example.com",
      );
    });
  });

  it("fechar a única aba mantém uma aba em branco, nunca lista vazia", async () => {
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);

    const urlBar = await screen.findByTestId("browser-url-bar");
    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "example.com" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });
    await screen.findByTitle("Browser");

    const items = screen.getAllByTestId("browser-tab-strip-item");
    expect(items).toHaveLength(1);
    const closeBtn = items[0].querySelector(
      `[title="workbench_browser_close_tab"]`,
    ) as HTMLElement;
    fireEvent.click(closeBtn);

    expect(screen.getAllByTestId("browser-tab-strip-item")).toHaveLength(1);
    expect(document.querySelector("iframe")).toBeNull();
  });

  it("servidor de dev que sobe via poll abre em aba nova, sem fechar/substituir a aba já aberta pelo usuário", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let running = false;
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith("/browser/launch")) {
        return Promise.resolve({
          ok: true,
          json: async () => LAUNCH,
        } as Response);
      }
      if (url.endsWith("/browser/status")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            servers: [
              { name: "web", port: 3001, running, pid: running ? 1 : null },
            ],
          }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });

    render(<BrowserTab threadId="t1" />);

    // Usuário já navegou manualmente na aba original antes do servidor subir.
    const urlBar = await screen.findByTestId("browser-url-bar");
    fireEvent.focus(urlBar);
    fireEvent.change(urlBar, { target: { value: "example.com" } });
    fireEvent.keyDown(urlBar, { key: "Enter" });
    await screen.findByTitle("Browser");

    running = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    // O servidor abriu numa aba NOVA (2 abas agora), a nova ficou ativa.
    await waitFor(() => {
      expect(screen.getAllByTestId("browser-tab-strip-item")).toHaveLength(2);
    });
    expect(document.querySelector("iframe")?.getAttribute("src")).toBe(
      "http://localhost:3001",
    );

    // A aba original com example.com continua intacta — voltando pra ela.
    const items = screen.getAllByTestId("browser-tab-strip-item");
    fireEvent.click(items[0]);
    expect(document.querySelector("iframe")?.getAttribute("src")).toBe(
      "https://example.com",
    );

    vi.useRealTimers();
  });
});

describe("BrowserTab — múltiplas abas no caminho desktop (WebContentsView por aba)", () => {
  type EventHandler = (
    viewId: number,
    event: {
      type: string;
      url?: string;
      canGoBack?: boolean;
      canGoForward?: boolean;
    },
  ) => void;

  function mockBrowserViewMultiTab() {
    let nextViewId = 1;
    let handler: EventHandler | null = null;
    const bridge = {
      createView: vi.fn(async () => nextViewId++),
      destroyView: vi.fn(),
      navigate: vi.fn(async () => ({ ok: true })),
      goBack: vi.fn(),
      goForward: vi.fn(),
      reload: vi.fn(),
      setBounds: vi.fn(),
      setVisible: vi.fn(),
      onEvent: vi.fn((h: EventHandler) => {
        handler = h;
        return () => {
          handler = null;
        };
      }),
      emitEvent: (viewId: number, event: Parameters<EventHandler>[1]) =>
        handler?.(viewId, event),
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).vectora = { browserView: bridge };
    return bridge;
  }

  afterEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (window as any).vectora;
  });

  it("cada aba nasce com sua própria WebContentsView — trocar de aba esconde a antiga e mostra a nova", async () => {
    const bridge = mockBrowserViewMultiTab();
    mockFetch({ configurations: [] });
    render(<BrowserTab threadId="t1" />);
    await waitFor(() => expect(bridge.createView).toHaveBeenCalledTimes(1));

    fireEvent.click(await screen.findByTitle("workbench_browser_new_tab"));
    await waitFor(() => expect(bridge.createView).toHaveBeenCalledTimes(2));

    // setVisible reflete: view 1 (aba antiga) escondida, view 2 (nova, ativa) visível.
    await waitFor(() => {
      expect(bridge.setVisible).toHaveBeenCalledWith(1, false);
      expect(bridge.setVisible).toHaveBeenCalledWith(2, true);
    });
  });

  it("desmontar com múltiplas abas oculta todas as WebContentsView, sem destruí-las", async () => {
    const bridge = mockBrowserViewMultiTab();
    mockFetch({ configurations: [] });
    const { unmount } = render(<BrowserTab threadId="t1" />);
    await waitFor(() => expect(bridge.createView).toHaveBeenCalledTimes(1));

    fireEvent.click(await screen.findByTitle("workbench_browser_new_tab"));
    await waitFor(() => expect(bridge.createView).toHaveBeenCalledTimes(2));

    unmount();
    expect(bridge.setVisible).toHaveBeenCalledWith(1, false);
    expect(bridge.setVisible).toHaveBeenCalledWith(2, false);
    expect(bridge.destroyView).not.toHaveBeenCalled();
  });
});
