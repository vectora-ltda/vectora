// @vitest-environment jsdom
import { describe, expect, it, afterEach, vi } from "vitest";
import { render, screen, cleanup, waitFor, act } from "@testing-library/react";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get:
        (_target, prop) =>
        (...args: unknown[]) =>
          args.length
            ? `${String(prop)}(${JSON.stringify(args[0])})`
            : String(prop),
    },
  ),
}));

vi.mock("@/lib/hooks/use-is-dark", () => ({
  useIsDark: () => false,
}));

vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: (sel: (s: { error: () => void }) => unknown) =>
    sel({ error: vi.fn() }),
}));

vi.mock("@/components/workbench/markdown-view", () => ({
  MarkdownView: ({ content }: { content: string }) => (
    <div data-testid="markdown-view">{content}</div>
  ),
}));

vi.mock("@/components/workbench/monaco-readonly", () => ({
  default: ({ value, path }: { value: string; path: string }) => (
    <div data-testid="monaco-readonly" data-path={path}>
      {value}
    </div>
  ),
}));

import {
  FileViewer,
  getMediaKind,
  rawFileUrl,
  MediaView,
} from "../file-viewer";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("getMediaKind / rawFileUrl", () => {
  it("classifica extensões conhecidas e retorna null para texto comum", () => {
    expect(getMediaKind("a.png")).toBe("image");
    expect(getMediaKind("a.mp4")).toBe("video");
    expect(getMediaKind("a.mp3")).toBe("audio");
    expect(getMediaKind("a.pdf")).toBe("pdf");
    expect(getMediaKind("a.ts")).toBeNull();
  });

  it("monta a URL raw com o path codificado como query string", () => {
    const url = rawFileUrl("ws 1", "src/a b.png");
    expect(url).toBe("/workspaces/ws%201/fs/raw?path=src%2Fa+b.png");
  });
});

describe("MediaView", () => {
  it("renderiza <img> para kind=image", () => {
    render(<MediaView kind="image" workspaceId="ws1" path="a.png" />);
    expect(screen.getByAltText("a.png")).toBeTruthy();
  });

  it("renderiza fallback de link para kind=pdf (object sem plugin no jsdom)", () => {
    render(<MediaView kind="pdf" workspaceId="ws1" path="a.pdf" />);
    expect(screen.getByText("a.pdf")).toBeTruthy();
  });
});

describe("FileViewer — dispatch por tipo de arquivo", () => {
  it("path de mídia (imagem) renderiza MediaView sem chamar fetch", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<FileViewer workspaceId="ws1" path="assets/logo.png" />);
    expect(await screen.findByAltText("assets/logo.png")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("arquivo binário mostra aviso de tamanho e link de download, sem Monaco", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ kind: "binary", size: 4096, truncated: false }),
      ),
    );
    render(<FileViewer workspaceId="ws1" path="bin/data.bin" />);
    await waitFor(() =>
      expect(screen.getByText(/workbench_files_binary/)).toBeTruthy(),
    );
    expect(screen.queryByTestId("monaco-readonly")).toBeNull();
  });

  it("arquivo .md com conteúdo renderiza via MarkdownView", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          kind: "text",
          content: "# Título",
          size: 8,
          truncated: false,
        }),
      ),
    );
    render(<FileViewer workspaceId="ws1" path="docs/readme.md" />);
    const md = await screen.findByTestId("markdown-view");
    expect(md.textContent).toBe("# Título");
  });

  it("arquivo de texto comum renderiza via MonacoReadOnly (lazy)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          kind: "text",
          content: "const x = 1;",
          size: 12,
          truncated: false,
        }),
      ),
    );
    render(<FileViewer workspaceId="ws1" path="src/a.ts" />);
    const editor = await screen.findByTestId("monaco-readonly");
    expect(editor.textContent).toBe("const x = 1;");
    expect(editor.getAttribute("data-path")).toBe("src/a.ts");
  });

  it("erro de fetch não quebra o render — cai no MonacoReadOnly com conteúdo vazio", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );
    expect(() =>
      render(<FileViewer workspaceId="ws1" path="src/erro.ts" />),
    ).not.toThrow();
    const editor = await screen.findByTestId("monaco-readonly");
    expect(editor.textContent).toBe("");
  });

  it("clicar em editar troca para o InlineTextEditor com o conteúdo carregado", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          kind: "text",
          content: "linha 1",
          size: 7,
          truncated: false,
        }),
      ),
    );
    render(<FileViewer workspaceId="ws1" path="src/a.ts" />);
    const editBtn = await screen.findByTitle("workbench_files_edit");
    await act(async () => {
      editBtn.click();
    });
    const textarea = document.querySelector("textarea") as HTMLTextAreaElement;
    expect(textarea.value).toBe("linha 1");
  });
});
