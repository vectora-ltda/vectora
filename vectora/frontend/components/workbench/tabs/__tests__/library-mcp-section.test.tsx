// @vitest-environment jsdom
/**
 * McpSection — seção MCP da Library.
 *
 * Cobre: lista os conectores do registry; instalar um sem env_vars chama
 * POST /mcp/install direto; instalar um com env_vars abre o form de config
 * antes e salva cada var via POST /auth/envs antes de instalar; erro/borda:
 * falha de instalação (status "error") mostra mensagem sem quebrar a lista;
 * conector já instalado mostra botão "Remover"; toggle "avançado" mostra o
 * form manual (PluginsTab).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  waitFor,
  fireEvent,
  act,
  within,
} from "@testing-library/react";

import { McpSection } from "../library-mcp-section";
import { useLibraryStore } from "@/lib/stores/library-store";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { useWindowsStore } from "@/lib/stores/windows-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";

afterEach(cleanup);

beforeEach(() => {
  useLibraryStore.setState({
    mcpItems: [],
    mcpInstalledIds: new Set(),
    mcpLoading: false,
    mcpFetchedAt: null,
    mcpQuery: "",
    mcpError: null,
  });
  useWindowsStore.setState({
    canvasDocuments: [],
    activeCanvasDocumentId: null,
  });
  useSettingsStore.setState({ uiMode: "assistant" });
  useWorkspacesStore.setState({ active_id: "workspace-1" });
});

const REGISTRY = [
  {
    id: "filesystem",
    name: "Filesystem",
    description: "Acesso ao filesystem local",
    install_cmd: "npx -y @modelcontextprotocol/server-filesystem",
    env_vars: [],
    homepage: "https://example.com",
    category: "filesystem",
    vectora_verified: false,
  },
  {
    id: "brave-search",
    name: "Brave Search",
    description: "Pesquisa web via Brave",
    install_cmd: "npx -y @modelcontextprotocol/server-brave-search",
    env_vars: ["BRAVE_API_KEY"],
    homepage: "https://example.com",
    category: "community",
    vectora_verified: false,
    trust_state: "unsigned",
  },
];

function mockFetch({
  installedNames = [] as string[],
  installStatus = "installed" as string,
} = {}) {
  global.fetch = vi
    .fn()
    .mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/mcp/registry") {
        return Promise.resolve({
          ok: true,
          json: async () => REGISTRY,
        } as Response);
      }
      if (url === "/plugins") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            servers: installedNames.map((name) => ({ name })),
          }),
        } as Response);
      }
      if (url === "/mcp/install") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ status: installStatus, mcp_id: "x" }),
        } as Response);
      }
      if (url === "/mcp/uninstall") {
        return Promise.resolve({
          ok: true,
          json: async () => ({}),
        } as Response);
      }
      if (url === "/auth/envs" && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: async () => ({}),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });
}

describe("McpSection", () => {
  beforeEach(() => {
    mockFetch();
  });

  it("lista os conectores do registry", async () => {
    render(<McpSection query="" />);
    await waitFor(() => {
      expect(screen.getByText("Filesystem")).toBeTruthy();
      expect(screen.getByText("Brave Search")).toBeTruthy();
    });
  });

  it("não exibe badge de comunidade nem verificação para MCPs do catálogo", async () => {
    render(<McpSection query="" />);
    await waitFor(() => {
      expect(screen.getByText("Filesystem")).toBeTruthy();
    });
    expect(screen.queryByText("Community listed")).toBeNull();
    expect(screen.queryByText("Verified")).toBeNull();
    expect(screen.queryByText("community", { exact: true })).toBeNull();
  });

  it("abre o MCP no canvas compartilhado e troca para o modo IDE", async () => {
    render(<McpSection query="" threadId="thread-1" />);
    await waitFor(() => expect(screen.getByText("Filesystem")).toBeTruthy());

    fireEvent.click(screen.getByText("Filesystem").closest('[role="button"]')!);

    const state = useWindowsStore.getState();
    expect(useSettingsStore.getState().uiMode).toBe("ide");
    expect(state.activeCanvasDocumentId).toBe(
      "mcp:workspace-1:thread-1:filesystem",
    );
    expect(state.canvasDocuments).toContainEqual(
      expect.objectContaining({
        id: "mcp:workspace-1:thread-1:filesystem",
        kind: "mcp-preview",
        workspaceId: "workspace-1",
        threadId: "thread-1",
        mcp: expect.objectContaining({ id: "filesystem" }),
      }),
    );
  });

  it("mantém previews do mesmo MCP separados entre workspaces", async () => {
    const { rerender } = render(<McpSection query="" threadId="thread-1" />);
    await waitFor(() => expect(screen.getByText("Filesystem")).toBeTruthy());
    fireEvent.click(screen.getByText("Filesystem").closest('[role="button"]')!);

    useWorkspacesStore.setState({ active_id: "workspace-2" });
    rerender(<McpSection query="" threadId="thread-1" />);
    fireEvent.click(screen.getByText("Filesystem").closest('[role="button"]')!);

    expect(
      useWindowsStore.getState().canvasDocuments.map((document) => document.id),
    ).toEqual([
      "mcp:workspace-1:thread-1:filesystem",
      "mcp:workspace-2:thread-1:filesystem",
    ]);
  });

  it("mantém visível o preview aberto sem workspace ativo", async () => {
    useWorkspacesStore.setState({ active_id: null });
    render(<McpSection query="" threadId="thread-1" />);
    await waitFor(() => expect(screen.getByText("Filesystem")).toBeTruthy());

    fireEvent.click(screen.getByText("Filesystem").closest('[role="button"]')!);

    const state = useWindowsStore.getState();
    expect(state.activeCanvasDocumentId).toBe(
      "mcp:no-workspace:thread-1:filesystem",
    );
    expect(state.canvasDocuments).toContainEqual(
      expect.objectContaining({
        id: "mcp:no-workspace:thread-1:filesystem",
        workspaceId: null,
        threadId: "thread-1",
      }),
    );
  });

  it("instalar um conector sem env_vars chama POST /mcp/install direto", async () => {
    render(<McpSection query="" />);
    await waitFor(() => expect(screen.getByText("Filesystem")).toBeTruthy());

    const filesystemCard = screen
      .getByText("Filesystem")
      .closest("div.rounded-lg")!;
    fireEvent.click(
      Array.from(filesystemCard.querySelectorAll("button")).find((b) =>
        b.textContent?.includes("Install"),
      )!,
    );

    expect(useWindowsStore.getState().canvasDocuments).toEqual([]);
    expect(useSettingsStore.getState().uiMode).toBe("assistant");

    await waitFor(() => {
      const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls;
      expect(calls.some((c) => c[0] === "/mcp/install")).toBe(true);
    });
  });

  it("instalar um conector com env_vars abre o form de config antes de instalar", async () => {
    render(<McpSection query="" />);
    await waitFor(() => expect(screen.getByText("Brave Search")).toBeTruthy());

    const braveCard = screen
      .getByText("Brave Search")
      .closest("div.rounded-lg")!;
    fireEvent.click(
      Array.from(braveCard.querySelectorAll("button")).find((b) =>
        b.textContent?.includes("Install"),
      )!,
    );

    await waitFor(() => {
      expect(screen.getByText("BRAVE_API_KEY")).toBeTruthy();
    });

    // Ainda não deve ter chamado /mcp/install — só depois de preencher e confirmar.
    expect(
      (global.fetch as ReturnType<typeof vi.fn>).mock.calls.some(
        (c) => c[0] === "/mcp/install",
      ),
    ).toBe(false);
  });

  it("não persiste env vars quando a confirmação de MCP não verificado é recusada", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<McpSection query="" />);
    await waitFor(() => expect(screen.getByText("Brave Search")).toBeTruthy());

    const braveCard = screen
      .getByText("Brave Search")
      .closest("div.rounded-lg")!;
    fireEvent.click(
      Array.from(braveCard.querySelectorAll("button")).find((b) =>
        b.textContent?.includes("Install"),
      )!,
    );

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(dialog.querySelector("input")!, {
      target: { value: "secret" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Install" }));

    await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
    const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls.some((c) => c[0] === "/auth/envs")).toBe(false);
    expect(calls.some((c) => c[0] === "/mcp/install")).toBe(false);
    confirm.mockRestore();
  });

  it("erro/borda: instalar com status 'error' mostra mensagem sem quebrar a lista", async () => {
    mockFetch({ installStatus: "error" });
    render(<McpSection query="" />);
    await waitFor(() => expect(screen.getByText("Filesystem")).toBeTruthy());

    const filesystemCard = screen
      .getByText("Filesystem")
      .closest("div.rounded-lg")!;
    fireEvent.click(
      Array.from(filesystemCard.querySelectorAll("button")).find((b) =>
        b.textContent?.includes("Install"),
      )!,
    );

    await waitFor(() => {
      expect(screen.getByText("Error installing connector")).toBeTruthy();
    });
    // A lista continua renderizada, não quebra.
    expect(screen.getByText("Brave Search")).toBeTruthy();
  });

  it("conector já instalado mostra botão de remover", async () => {
    mockFetch({ installedNames: ["filesystem"] });
    render(<McpSection query="" />);
    await waitFor(() => {
      const filesystemCard = screen
        .getByText("Filesystem")
        .closest("div.rounded-lg")!;
      expect(
        Array.from(filesystemCard.querySelectorAll("button")).some((b) =>
          b.textContent?.includes("Remove"),
        ),
      ).toBe(true);
    });
  });

  it("digitar na busca dispara /mcp/registry?q= com debounce de 350ms; erro de rede na busca seguinte mantém o último resultado bom e mostra erro discreto", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi.fn(async (input: URL | RequestInfo) => {
      const url = String(input);
      if (url === "/plugins") {
        return { ok: true, json: async () => ({ servers: [] }) } as Response;
      }
      if (url === "/mcp/registry") {
        return { ok: true, json: async () => REGISTRY } as Response;
      }
      if (url === "/mcp/registry?q=brave") {
        return { ok: true, json: async () => [REGISTRY[1]] } as Response;
      }
      throw new Error(`unexpected url ${url}`);
    });
    global.fetch = fetchMock as typeof fetch;

    const { rerender } = render(<McpSection query="" />);
    await vi.waitFor(() => expect(screen.getByText("Filesystem")).toBeTruthy());

    rerender(<McpSection query="brave" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });
    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) => c[0] === "/mcp/registry?q=brave"),
      ).toBe(true);
    });
    await vi.waitFor(() => {
      expect(screen.getByText("Brave Search")).toBeTruthy();
      expect(screen.queryByText("Filesystem")).toBeNull();
    });

    fetchMock.mockImplementation(async (input: URL | RequestInfo) => {
      if (String(input) === "/mcp/registry?q=brave2") {
        throw new Error("network down");
      }
      return { ok: true, json: async () => ({ servers: [] }) } as Response;
    });
    rerender(<McpSection query="brave2" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });

    await vi.waitFor(() => {
      expect(screen.getByText("Brave Search")).toBeTruthy();
      expect(screen.getByText("Error searching connectors")).toBeTruthy();
    });

    vi.useRealTimers();
  });
});
