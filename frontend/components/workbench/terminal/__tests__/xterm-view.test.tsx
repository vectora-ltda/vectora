// @vitest-environment jsdom
/**
 * XtermView — liga o terminal ao WebSocket do PTY no backend. xterm.js e
 * seus addons são mockados por fakes mínimos (o componente só usa
 * new/loadAddon/open/write/onData/dispose/options.theme); o WebSocket real do
 * jsdom é substituído por um fake controlável para simular open/message/
 * error/close sem rede.
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  cleanup,
  act,
  waitFor,
  fireEvent,
} from "@testing-library/react";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_t, prop) => () => String(prop) }),
}));

vi.mock("@xterm/xterm/css/xterm.css", () => ({}));

class FakeTerminal {
  static instances: FakeTerminal[] = [];
  options: Record<string, unknown>;
  cols = 80;
  rows = 24;
  written: Array<string | Uint8Array> = [];
  disposed = false;
  focusCalls = 0;
  private dataHandler: ((d: string) => void) | null = null;
  constructor(options: Record<string, unknown>) {
    this.options = options;
    FakeTerminal.instances.push(this);
  }
  loadAddon(): void {}
  open(): void {}
  focus(): void {
    this.focusCalls++;
  }
  write(data: string | Uint8Array): void {
    this.written.push(data);
  }
  onData(cb: (d: string) => void): void {
    this.dataHandler = cb;
  }
  dispose(): void {
    this.disposed = true;
  }
  emitData(d: string): void {
    this.dataHandler?.(d);
  }
}

class FakeFitAddon {
  static instances: FakeFitAddon[] = [];
  fitCalls = 0;
  constructor() {
    FakeFitAddon.instances.push(this);
  }
  fit(): void {
    this.fitCalls++;
  }
}

// oxlint-disable-next-line typescript/no-extraneous-class -- mock constructível p/ vi.mock, sem estado próprio
class FakeWebLinksAddon {}

vi.mock("@xterm/xterm", () => ({ Terminal: FakeTerminal }));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: FakeFitAddon }));
vi.mock("@xterm/addon-web-links", () => ({ WebLinksAddon: FakeWebLinksAddon }));

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSED = 3;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string | ArrayBuffer }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  readyState = FakeWebSocket.CONNECTING;
  binaryType = "blob";
  sent: Array<string | ArrayBuffer | Uint8Array> = [];
  private listeners: Record<string, Array<(ev: unknown) => void>> = {};
  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  addEventListener(type: string, cb: (ev: unknown) => void): void {
    (this.listeners[type] ??= []).push(cb);
  }
  send(data: string | ArrayBuffer | Uint8Array): void {
    this.sent.push(data);
  }
  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.dispatch("close", undefined);
  }
  dispatch(type: string, ev: unknown): void {
    for (const cb of this.listeners[type] ?? []) cb(ev);
  }
  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.dispatch("open", undefined);
  }
  message(data: string | ArrayBuffer): void {
    this.dispatch("message", { data });
  }
  error(): void {
    this.dispatch("error", {});
  }
}

function ThrowingWebSocket(): never {
  throw new DOMException("URL relativa inválida", "SyntaxError");
}

class FakeResizeObserver {
  static instances: FakeResizeObserver[] = [];
  observed: Element[] = [];
  disconnected = false;
  constructor(private cb: () => void) {
    FakeResizeObserver.instances.push(this);
  }
  observe(el: Element): void {
    this.observed.push(el);
  }
  disconnect(): void {
    this.disconnected = true;
  }
  trigger(): void {
    this.cb();
  }
}

const fetchMock = vi.fn(async (url: string) => {
  if (String(url).includes("/auth/ws-token")) {
    return new Response(JSON.stringify({ token: "tok123" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  return new Response("{}", { status: 200 });
});

beforeEach(() => {
  FakeTerminal.instances = [];
  FakeFitAddon.instances = [];
  FakeWebSocket.instances = [];
  FakeResizeObserver.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

import { XtermView } from "../xterm-view";

async function renderView(props?: Partial<Parameters<typeof XtermView>[0]>) {
  const wsCountBefore = FakeWebSocket.instances.length;
  const termCountBefore = FakeTerminal.instances.length;
  const utils = render(
    <XtermView
      terminalId="term-1"
      threadId="thread-1"
      workspaceId="ws-1"
      {...props}
    />,
  );
  await waitFor(() =>
    expect(FakeWebSocket.instances.length).toBeGreaterThan(wsCountBefore),
  );
  const ws = FakeWebSocket.instances[wsCountBefore];
  await waitFor(() =>
    expect(FakeTerminal.instances.length).toBeGreaterThan(termCountBefore),
  );
  const term = FakeTerminal.instances[termCountBefore];
  return { ...utils, ws, term };
}

describe("XtermView", () => {
  it("monta o Terminal e conecta ao WebSocket com terminal_id/thread_id/workspace_id/token na URL", async () => {
    const { ws } = await renderView();
    expect(ws.url).toContain("terminal_id=term-1");
    expect(ws.url).toContain("thread_id=thread-1");
    expect(ws.url).toContain("workspace_id=ws-1");
    expect(ws.url).toContain("token=tok123");
  });

  it("ao abrir a conexão (onopen) envia resize com cols/rows do terminal", async () => {
    const { ws, term } = await renderView();
    await act(async () => {
      ws.open();
    });
    const resizeMsg = ws.sent.find(
      (s) => typeof s === "string" && s.includes('"type":"resize"'),
    ) as string;
    expect(resizeMsg).toBeDefined();
    const parsed = JSON.parse(resizeMsg);
    expect(parsed).toEqual({
      type: "resize",
      cols: term.cols,
      rows: term.rows,
    });
  });

  it("digitar no terminal (onData) envia os bytes pelo WebSocket quando aberto", async () => {
    const { ws, term } = await renderView();
    await act(async () => {
      ws.open();
    });
    await act(async () => {
      term.emitData("ls -la\n");
    });
    expect(ws.sent.length).toBeGreaterThan(0);
    const last = ws.sent[ws.sent.length - 1] as Uint8Array;
    expect(new TextDecoder().decode(last)).toBe("ls -la\n");
  });

  it("chama term.focus() ao montar — sem isso, digitar exige clicar primeiro", async () => {
    const { term } = await renderView();
    expect(term.focusCalls).toBeGreaterThan(0);
  });

  it("clicar no container refoca o terminal", async () => {
    const { term, container } = await renderView();
    const focusCallsAfterMount = term.focusCalls;
    fireEvent.click(container.firstChild as Element);
    expect(term.focusCalls).toBeGreaterThan(focusCallsAfterMount);
  });

  it("onData não envia nada quando o WebSocket ainda não está aberto (readyState != OPEN)", async () => {
    const { ws, term } = await renderView();
    // readyState continua CONNECTING — não chamamos ws.open()
    await act(async () => {
      term.emitData("comando ignorado");
    });
    expect(ws.sent).toHaveLength(0);
  });

  it("mensagem de texto binária (ArrayBuffer) é escrita no terminal como Uint8Array", async () => {
    const { ws, term } = await renderView();
    const buf = new TextEncoder().encode("saida crua").buffer;
    await act(async () => {
      ws.message(buf);
    });
    const last = term.written[term.written.length - 1] as Uint8Array;
    expect(new TextDecoder().decode(last)).toBe("saida crua");
  });

  it("mensagem JSON {type: error} escreve a mensagem de erro no terminal", async () => {
    const { ws, term } = await renderView();
    await act(async () => {
      ws.message(JSON.stringify({ type: "error", message: "comando falhou" }));
    });
    const last = term.written[term.written.length - 1] as string;
    expect(last).toContain("comando falhou");
  });

  it("mensagem JSON {type: closed} escreve aviso de encerramento e chama onClosed", async () => {
    const onClosed = vi.fn();
    const { ws, term } = await renderView({ onClosed });
    await act(async () => {
      ws.message(JSON.stringify({ type: "closed" }));
    });
    expect(term.written.join("")).toContain("terminal_ended");
    expect(onClosed).toHaveBeenCalledTimes(1);
  });

  it("mensagem de texto malformada (não-JSON) é escrita crua no terminal, sem lançar exceção", async () => {
    const { ws, term } = await renderView();
    await act(async () => {
      ws.message("saida de texto solta, nao é json {");
    });
    const last = term.written[term.written.length - 1];
    expect(last).toBe("saida de texto solta, nao é json {");
  });

  it("erro de conexão (onerror) escreve mensagem de erro de conexão no terminal", async () => {
    const { ws, term } = await renderView();
    await act(async () => {
      ws.error();
    });
    expect(term.written.join("")).toContain("terminal_conn_error");
  });

  it("fechamento inesperado do socket (onclose) propaga onClosed quando o componente ainda está montado", async () => {
    const onClosed = vi.fn();
    const { ws } = await renderView({ onClosed });
    await act(async () => {
      ws.close();
    });
    expect(onClosed).toHaveBeenCalledTimes(1);
  });

  it("desmontar o componente fecha o WebSocket, desconecta observers e não chama onClosed pela desmontagem", async () => {
    const onClosed = vi.fn();
    const { ws, unmount } = await renderView({ onClosed });
    unmount();
    expect(ws.readyState).toBe(FakeWebSocket.CLOSED);
    expect(onClosed).not.toHaveBeenCalled();
  });

  it("resize do container (ResizeObserver) reenvia resize pelo WebSocket quando aberto", async () => {
    const { ws } = await renderView();
    await act(async () => {
      ws.open();
    });
    const initialResizeCount = ws.sent.filter(
      (s) => typeof s === "string" && s.includes('"type":"resize"'),
    ).length;
    const ro = FakeResizeObserver.instances[0];
    await act(async () => {
      ro.trigger();
    });
    const afterResizeCount = ws.sent.filter(
      (s) => typeof s === "string" && s.includes('"type":"resize"'),
    ).length;
    expect(afterResizeCount).toBeGreaterThan(initialResizeCount);
  });

  it("múltiplas instâncias (terminalId diferentes) abrem WebSockets distintos com URLs próprias", async () => {
    const { ws: ws1 } = await renderView({ terminalId: "term-a" });
    const { ws: ws2 } = await renderView({ terminalId: "term-b" });
    expect(ws1.url).toContain("terminal_id=term-a");
    expect(ws2.url).toContain("terminal_id=term-b");
    expect(ws1).not.toBe(ws2);
  });

  it("falha ao buscar o token (/auth/ws-token não-ok) ainda conecta o WebSocket, com token vazio na URL", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("erro", { status: 500 })),
    );
    const { ws } = await renderView();
    expect(ws.url.endsWith("token=")).toBe(true);
  });

  describe("origem do WebSocket no desktop Electron", () => {
    afterEach(() => {
      delete (window as { vectora?: unknown }).vectora;
    });

    it("com window.vectora.getBackendWsOrigin disponível: usa a origem absoluta do backend, não uma URL relativa", async () => {
      window.vectora = {
        getBackendWsOrigin: async () => "ws://127.0.0.1:54321",
      } as Window["vectora"];
      const { ws } = await renderView();
      expect(ws.url.startsWith("ws://127.0.0.1:54321/")).toBe(true);
    });

    it("sem window.vectora (browser/dev server): mantém a resolução relativa via VECTORA_API_URL (regressão)", async () => {
      const { ws } = await renderView();
      expect(ws.url.startsWith("/vectora.terminal.v1/ws")).toBe(true);
    });

    it("construtor do WebSocket lançando (ex.: URL relativa contra scheme custom) escreve erro no terminal em vez de quebrar o componente", async () => {
      window.vectora = {
        getBackendWsOrigin: async () => null,
      } as Window["vectora"];
      vi.stubGlobal("WebSocket", ThrowingWebSocket);
      const utils = render(
        <XtermView
          terminalId="term-x"
          threadId="thread-x"
          workspaceId="ws-x"
        />,
      );
      await waitFor(() =>
        expect(FakeTerminal.instances.length).toBeGreaterThan(0),
      );
      const term = FakeTerminal.instances[FakeTerminal.instances.length - 1];
      await waitFor(() =>
        expect(term.written.join("")).toContain("terminal_conn_error"),
      );
      utils.unmount();
    });
  });
});
