// @vitest-environment jsdom
import { describe, expect, it, afterEach, vi } from "vitest";
import { render, screen, cleanup, waitFor, act } from "@testing-library/react";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get:
        (_target, prop) =>
        (..._args: unknown[]) =>
          String(prop),
    },
  ),
}));

import { MarkdownPreviewDialog } from "../markdown-preview-dialog";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("MarkdownPreviewDialog", () => {
  it("fechado (open=false) não renderiza o conteúdo do dialog", () => {
    render(
      <MarkdownPreviewDialog
        open={false}
        onOpenChange={vi.fn()}
        content="# Olá"
      />,
    );
    expect(screen.queryByText("Olá")).toBeNull();
  });

  it("aberto com `content` fixo exibe o markdown renderizado sem buscar via fetch", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MarkdownPreviewDialog
        open={true}
        onOpenChange={vi.fn()}
        filePath="docs/readme.md"
        content="# Olá mundo"
      />,
    );
    expect(
      await screen.findByRole("heading", { name: "Olá mundo" }),
    ).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("aberto sem `content` busca via fetch usando `filePath` e exibe o resultado", async () => {
    const fetchMock = vi.fn(
      async () => new Response("# Carregado do backend", { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MarkdownPreviewDialog
        open={true}
        onOpenChange={vi.fn()}
        filePath="docs/readme.md"
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Carregado do backend" }),
      ).toBeTruthy(),
    );
    expect(fetchMock).toHaveBeenCalledWith("docs/readme.md");
  });

  it("erro de fetch (rede indisponível) mostra o estado vazio em vez de travar ou lançar", async () => {
    const fetchMock = vi.fn(async () => {
      throw new Error("network down");
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MarkdownPreviewDialog
        open={true}
        onOpenChange={vi.fn()}
        filePath="docs/inexistente.md"
      />,
    );
    await waitFor(() =>
      expect(screen.getByText("workbench_preview_md_empty")).toBeTruthy(),
    );
  });

  it("usa `filePath` como título quando presente", async () => {
    vi.stubGlobal("fetch", vi.fn());
    render(
      <MarkdownPreviewDialog
        open={true}
        onOpenChange={vi.fn()}
        filePath="docs/readme.md"
        content="conteúdo"
      />,
    );
    expect(await screen.findByText("docs/readme.md")).toBeTruthy();
  });

  it("clicar no botão de fechar do dialog propaga onOpenChange(false)", async () => {
    const onOpenChange = vi.fn();
    render(
      <MarkdownPreviewDialog
        open={true}
        onOpenChange={onOpenChange}
        content="texto"
      />,
    );
    const closeBtn = screen.getByText("Close").closest("button") as HTMLElement;
    await act(async () => {
      closeBtn.click();
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
