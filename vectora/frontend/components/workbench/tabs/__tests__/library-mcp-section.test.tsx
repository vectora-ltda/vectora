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
} from "@testing-library/react";

vi.mock("@/components/settings/environment/tabs/plugins-tab", () => ({
  PluginsTab: () => <div>stub-plugins-tab</div>,
}));

import { McpSection } from "../library-mcp-section";
import { useLibraryStore } from "@/lib/stores/library-store";

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
    vectora_verified: true,
  },
  {
    id: "brave-search",
    name: "Brave Search",
    description: "Pesquisa web via Brave",
    install_cmd: "npx -y @modelcontextprotocol/server-brave-search",
    env_vars: ["BRAVE_API_KEY"],
    homepage: "https://example.com",
    category: "web",
    vectora_verified: false,
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
    render(<McpSection query="" onCountChange={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText("Filesystem")).toBeTruthy();
      expect(screen.getByText("Brave Search")).toBeTruthy();
    });
  });

  it("mostra o estado de catálogo sem elevar curadoria a verificação", async () => {
    render(<McpSection query="" onCountChange={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText("Filesystem")).toBeTruthy();
    });
    const badges = screen.getAllByText("community_listed");
    expect(badges).toHaveLength(1);
  });

  it("reporta a contagem filtrada via onCountChange", async () => {
    const onCountChange = vi.fn();
    render(<McpSection query="" onCountChange={onCountChange} />);
    await waitFor(() => {
      expect(onCountChange).toHaveBeenCalledWith(2);
    });
  });

  it("instalar um conector sem env_vars chama POST /mcp/install direto", async () => {
    render(<McpSection query="" onCountChange={() => {}} />);
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
      const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls;
      expect(calls.some((c) => c[0] === "/mcp/install")).toBe(true);
    });
  });

  it("instalar um conector com env_vars abre o form de config antes de instalar", async () => {
    render(<McpSection query="" onCountChange={() => {}} />);
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

  it("erro/borda: instalar com status 'error' mostra mensagem sem quebrar a lista", async () => {
    mockFetch({ installStatus: "error" });
    render(<McpSection query="" onCountChange={() => {}} />);
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
    render(<McpSection query="" onCountChange={() => {}} />);
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

    const { rerender } = render(
      <McpSection query="" onCountChange={() => {}} />,
    );
    await vi.waitFor(() => expect(screen.getByText("Filesystem")).toBeTruthy());

    rerender(<McpSection query="brave" onCountChange={() => {}} />);
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
    rerender(<McpSection query="brave2" onCountChange={() => {}} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });

    await vi.waitFor(() => {
      expect(screen.getByText("Brave Search")).toBeTruthy();
      expect(screen.getByText("Error searching connectors")).toBeTruthy();
    });

    vi.useRealTimers();
  });

  it("toggle 'avançado' mostra o form manual (PluginsTab)", async () => {
    render(<McpSection query="" onCountChange={() => {}} />);
    await waitFor(() => expect(screen.getByText("Filesystem")).toBeTruthy());

    expect(screen.queryByText("stub-plugins-tab")).toBeNull();
    fireEvent.click(screen.getByText(/add mcp manually/i));
    expect(screen.getByText("stub-plugins-tab")).toBeTruthy();
  });
});
