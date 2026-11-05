// @vitest-environment jsdom
/**
 * TerminalPanel — tabs por instância, auto-abertura da primeira aba,
 * troca de aba ativa e fechamento. XtermView é mockado como componente
 * burro: o que interessa aqui é o container de abas, não o PTY em si
 * (isso é coberto em xterm-view.test.tsx).
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";

import type { TerminalInstance } from "@/lib/stores/terminals-store";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_t, prop) => () => String(prop) }),
}));

const mockOpen = vi.fn();
const mockClose = vi.fn();
const mockSetActive = vi.fn();

let mockList: TerminalInstance[] = [];
let mockActiveId: string | null = null;

const mockTerminalsState = {
  list: (_threadId: string) => mockList,
  active: (_threadId: string) =>
    mockList.find((t) => t.id === mockActiveId) ?? null,
  open: mockOpen,
  close: mockClose,
  setActive: mockSetActive,
};

function useTerminalsStoreMock(sel: (s: typeof mockTerminalsState) => unknown) {
  return sel(mockTerminalsState);
}
useTerminalsStoreMock.getState = () => mockTerminalsState;

vi.mock("@/lib/stores/terminals-store", () => ({
  useTerminalsStore: useTerminalsStoreMock,
}));

let mockWorkspace: {
  id: string;
  name: string;
  cwd: string;
  trusted: boolean;
  is_git_repo: boolean;
} | null = {
  id: "ws1",
  name: "proj",
  cwd: "/proj",
  trusted: true,
  is_git_repo: false,
};

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (sel: (s: { getActive: () => unknown }) => unknown) =>
    sel({ getActive: () => mockWorkspace }),
}));

vi.mock("../xterm-view", () => ({
  XtermView: (props: {
    terminalId: string;
    threadId: string;
    workspaceId: string;
    onClosed?: () => void;
  }) => (
    <div
      data-testid="xterm-view"
      data-terminal-id={props.terminalId}
      data-thread-id={props.threadId}
      data-workspace-id={props.workspaceId}
      onClick={() => props.onClosed?.()}
    />
  ),
}));

import { TerminalPanel } from "../terminal-panel";

let sandboxStatusResponse: { enabled: boolean } | null = { enabled: false };

const fetchMock = vi.fn(async () => {
  if (sandboxStatusResponse === null) {
    return new Response("not found", { status: 404 });
  }
  return new Response(JSON.stringify(sandboxStatusResponse), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
});

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  mockList = [];
  mockActiveId = null;
  sandboxStatusResponse = { enabled: false };
  mockWorkspace = {
    id: "ws1",
    name: "proj",
    cwd: "/proj",
    trusted: true,
    is_git_repo: false,
  };
});

function term(
  id: string,
  title: string,
  workspaceId = "ws1",
): TerminalInstance {
  return { id, title, workspaceId };
}

// TerminalPanel dispara um fetch (status do sandbox) no mount; sem isso, o
// setState da resposta acontece depois do teste terminar e fora de act().
async function flush(): Promise<void> {
  await act(async () => {});
}

describe("TerminalPanel", () => {
  it("sem workspace ativo: mostra mensagem de nenhum workspace", async () => {
    mockWorkspace = null;
    render(<TerminalPanel threadId="t1" />);
    await flush();
    expect(screen.getByText("terminal_no_workspace")).toBeInTheDocument();
  });

  it("workspace não confiável: mostra aviso e não abre terminal automático", async () => {
    mockWorkspace = {
      id: "ws1",
      name: "proj",
      cwd: "/proj",
      trusted: false,
      is_git_repo: false,
    };
    render(<TerminalPanel threadId="t1" />);
    await flush();
    expect(screen.getByText("terminal_untrusted_title")).toBeInTheDocument();
    expect(mockOpen).not.toHaveBeenCalled();
  });

  it("workspace confiável sem terminais: abre 1 terminal automaticamente ao montar", async () => {
    mockList = [];
    render(<TerminalPanel threadId="t1" />);
    await flush();
    expect(mockOpen).toHaveBeenCalledTimes(1);
    const [threadId, instance] = mockOpen.mock.calls[0];
    expect(threadId).toBe("t1");
    expect(instance.workspaceId).toBe("ws1");
    expect(typeof instance.id).toBe("string");
  });

  it("workspace confiável com terminais existentes: não chama open() automaticamente", async () => {
    mockList = [term("a", "shell 1")];
    mockActiveId = "a";
    render(<TerminalPanel threadId="t1" />);
    await flush();
    expect(mockOpen).not.toHaveBeenCalled();
  });

  it("renderiza uma aba por terminal com o título correto", async () => {
    mockList = [term("a", "shell 1"), term("b", "shell 2")];
    mockActiveId = "a";
    render(<TerminalPanel threadId="t1" />);
    await flush();
    expect(screen.getByText("shell 1")).toBeInTheDocument();
    expect(screen.getByText("shell 2")).toBeInTheDocument();
  });

  it("clicar no botão + (nova aba) chama open() com o threadId correto", async () => {
    mockList = [term("a", "shell 1")];
    mockActiveId = "a";
    render(<TerminalPanel threadId="t1" />);
    await flush();
    fireEvent.click(screen.getByTitle("terminal_new"));
    expect(mockOpen).toHaveBeenCalledTimes(1);
    expect(mockOpen.mock.calls[0][0]).toBe("t1");
  });

  it("clicar em uma aba inativa chama setActive com o id certo", async () => {
    mockList = [term("a", "shell 1"), term("b", "shell 2")];
    mockActiveId = "a";
    render(<TerminalPanel threadId="t1" />);
    await flush();
    fireEvent.click(screen.getByText("shell 2"));
    expect(mockSetActive).toHaveBeenCalledWith("t1", "b");
  });

  it("clicar no X de uma aba chama close() sem também disparar setActive (stopPropagation)", async () => {
    mockList = [term("a", "shell 1")];
    mockActiveId = "a";
    const { container } = render(<TerminalPanel threadId="t1" />);
    await flush();
    const closeHandle = container.querySelector(
      "[role='button']",
    ) as HTMLElement;
    expect(closeHandle).not.toBeNull();
    fireEvent.click(closeHandle);
    expect(mockClose).toHaveBeenCalledWith("t1", "a");
    expect(mockSetActive).not.toHaveBeenCalled();
  });

  it("aba sem terminal algum (list vazia após montar) não quebra a renderização das abas", async () => {
    mockList = [];
    mockActiveId = null;
    render(<TerminalPanel threadId="t1" />);
    await flush();
    // só o botão de nova aba deve existir, nenhuma aba de terminal
    expect(screen.queryByTestId("xterm-view")).not.toBeInTheDocument();
    expect(screen.getByTitle("terminal_new")).toBeInTheDocument();
  });

  it("renderiza um XtermView por terminal, passando terminalId/threadId/workspaceId", async () => {
    mockList = [term("a", "shell 1", "ws1"), term("b", "shell 2", "ws1")];
    mockActiveId = "a";
    render(<TerminalPanel threadId="t1" />);
    await flush();
    const views = screen.getAllByTestId("xterm-view");
    expect(views).toHaveLength(2);
    expect(views[0]).toHaveAttribute("data-terminal-id", "a");
    expect(views[0]).toHaveAttribute("data-thread-id", "t1");
    expect(views[0]).toHaveAttribute("data-workspace-id", "ws1");
  });

  it("só o terminal ativo fica visível (visibility:visible); os demais ficam hidden", async () => {
    mockList = [term("a", "shell 1"), term("b", "shell 2")];
    mockActiveId = "b";
    const { container } = render(<TerminalPanel threadId="t1" />);
    await flush();
    const wrappers = container.querySelectorAll(".absolute.inset-0");
    expect(wrappers[0]).toHaveStyle({ visibility: "hidden" });
    expect(wrappers[1]).toHaveStyle({ visibility: "visible" });
  });

  it("XtermView chamando onClosed() propaga para close() do store", async () => {
    mockList = [term("a", "shell 1")];
    mockActiveId = "a";
    render(<TerminalPanel threadId="t1" />);
    await flush();
    fireEvent.click(screen.getByTestId("xterm-view"));
    expect(mockClose).toHaveBeenCalledWith("t1", "a");
  });

  describe("indicador de sandbox", () => {
    it("workspace sem [sandbox]: mostra o aviso âmbar de sempre", async () => {
      sandboxStatusResponse = { enabled: false };
      render(<TerminalPanel threadId="t1" />);
      await waitFor(() =>
        expect(
          screen.getByText("terminal_no_sandbox_warning"),
        ).toBeInTheDocument(),
      );
      expect(
        screen.queryByText("terminal_sandbox_active"),
      ).not.toBeInTheDocument();
    });

    it("workspace com [sandbox] habilitado: mostra o indicador verde, não o aviso âmbar", async () => {
      sandboxStatusResponse = { enabled: true };
      render(<TerminalPanel threadId="t1" />);
      await waitFor(() =>
        expect(screen.getByText("terminal_sandbox_active")).toBeInTheDocument(),
      );
      expect(
        screen.queryByText("terminal_no_sandbox_warning"),
      ).not.toBeInTheDocument();
    });

    it("antes da resposta chegar: não mostra nenhum dos dois avisos", () => {
      // fetch nunca resolve nesta chamada (promise pendente) — o hook
      // continua em estado "carregando" (enabled === null).
      vi.stubGlobal(
        "fetch",
        vi.fn(() => new Promise(() => {})),
      );
      render(<TerminalPanel threadId="t1" />);
      expect(
        screen.queryByText("terminal_no_sandbox_warning"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText("terminal_sandbox_active"),
      ).not.toBeInTheDocument();
    });

    it("erro de rede degrada pro aviso âmbar (nunca alega sandbox sem confirmar)", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(() => Promise.reject(new Error("network down"))),
      );
      render(<TerminalPanel threadId="t1" />);
      await waitFor(() =>
        expect(
          screen.getByText("terminal_no_sandbox_warning"),
        ).toBeInTheDocument(),
      );
    });
  });
});
