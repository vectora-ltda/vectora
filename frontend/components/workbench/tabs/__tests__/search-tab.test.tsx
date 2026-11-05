// @vitest-environment jsdom
/**
 * SearchTab — busca em filesystem do workspace via
 * GET /workspaces/:id/fs/search?q=.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

const mockWorkspace = { id: "ws1" } as { id: string } | null;

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (
    sel: (s: { getActive: () => typeof mockWorkspace }) => unknown,
  ) => sel({ getActive: () => mockWorkspace }),
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_t, prop) => () => String(prop) }),
}));

import { SearchTab } from "../search-tab";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function typeAndEnter(value: string) {
  const input = screen.getByPlaceholderText(
    "workbench_files_search_placeholder",
  );
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: "Enter" });
}

describe("SearchTab", () => {
  it("digitar termo e Enter dispara busca no endpoint correto", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ results: [] }),
    });
    render(<SearchTab />);
    typeAndEnter("foo");
    await screen.findByText("workbench_files_search_no_results");
    expect(fetchMock).toHaveBeenCalledWith("/workspaces/ws1/fs/search?q=foo");
  });

  it("resultados renderizados com nome do arquivo e contagem de hits", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        results: [
          {
            file: "src/main.ts",
            hits: [{ path: "src/main.ts", line: 3, text: "const foo = 1" }],
          },
        ],
      }),
    });
    render(<SearchTab />);
    typeAndEnter("foo");
    expect(await screen.findByText("src/main.ts")).toBeInTheDocument();
    expect(screen.getByText("(1)")).toBeInTheDocument();
    expect(screen.getByText(/const foo = 1/)).toBeInTheDocument();
  });

  it("estado vazio: resultados=[] mostra mensagem de nenhum resultado", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ results: [] }),
    });
    render(<SearchTab />);
    typeAndEnter("nada-encontrado");
    expect(
      await screen.findByText("workbench_files_search_no_results"),
    ).toBeInTheDocument();
  });

  it("erro: resposta não-ok mostra mensagem de erro tipada", async () => {
    fetchMock.mockResolvedValue({ ok: false, json: async () => ({}) });
    render(<SearchTab />);
    typeAndEnter("foo");
    expect(await screen.findByText("Search failed")).toBeInTheDocument();
  });

  it("erro: fetch rejeita (rede) cai no fallback de mensagem sem erro nativo", async () => {
    fetchMock.mockRejectedValue("network down");
    render(<SearchTab />);
    typeAndEnter("foo");
    expect(
      await screen.findByText("workbench_files_search_no_results"),
    ).toBeInTheDocument();
  });

  it("borda: query vazia (apenas espaços) não dispara fetch", () => {
    render(<SearchTab />);
    typeAndEnter("   ");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("borda: sem workspace ativo não dispara fetch", () => {
    mockWorkspace!.id = "";
    render(<SearchTab />);
    typeAndEnter("foo");
    expect(fetchMock).not.toHaveBeenCalled();
    mockWorkspace!.id = "ws1";
  });

  it("botão limpar reseta query, resultados e erro", async () => {
    fetchMock.mockResolvedValue({ ok: false, json: async () => ({}) });
    render(<SearchTab />);
    typeAndEnter("foo");
    await screen.findByText("Search failed");
    fireEvent.click(screen.getByLabelText("Clear search"));
    expect(
      screen.getByPlaceholderText("workbench_files_search_placeholder"),
    ).toHaveValue("");
    expect(screen.queryByText("Search failed")).not.toBeInTheDocument();
  });
});
