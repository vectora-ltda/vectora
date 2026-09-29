// @vitest-environment jsdom
import { describe, expect, it, afterEach, vi } from "vitest";
import {
  render,
  screen,
  cleanup,
  waitFor,
  act,
  fireEvent,
} from "@testing-library/react";

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

vi.mock("@monaco-editor/react", () => ({
  default: ({
    value,
    onChange,
    options,
  }: {
    value?: string;
    onChange?: (v: string | undefined) => void;
    options?: {
      readOnly?: boolean;
      fontSize?: number;
      mouseWheelZoom?: boolean;
    };
  }) => (
    <textarea
      data-testid="monaco-editor"
      data-readonly={String(!!options?.readOnly)}
      data-font-size={options?.fontSize}
      data-mouse-wheel-zoom={String(!!options?.mouseWheelZoom)}
      value={value ?? ""}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

vi.mock("@/lib/monaco/setup", () => ({
  languageFromPath: (path: string) =>
    path.endsWith(".py") ? "python" : "typescript",
}));

vi.mock("@/lib/hooks/use-is-dark", () => ({
  useIsDark: () => false,
}));

const mockSettings = { monacoFontSize: 13 };
vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (sel: (s: typeof mockSettings) => unknown) =>
    sel(mockSettings),
}));

const mockToastError = vi.fn();
vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: { getState: () => ({ error: mockToastError }) },
}));

vi.mock("@/components/workbench/file-viewer", () => ({
  getMediaKind: (path: string) => (path.endsWith(".png") ? "image" : null),
  FileViewer: ({ path }: { path: string }) => (
    <div data-testid="file-viewer-fallback">{path}</div>
  ),
}));

const fetchFile = vi.fn();
const apiUpdateFile = vi.fn();
vi.mock("@/lib/api/fs-files", () => ({
  fetchFile: (...args: unknown[]) => fetchFile(...args),
  apiUpdateFile: (...args: unknown[]) => apiUpdateFile(...args),
}));

import { FileEditor } from "../file-editor";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("FileEditor", () => {
  it("carrega o conteúdo do arquivo e exibe no editor Monaco", async () => {
    fetchFile.mockResolvedValue({
      content: "const x = 1;",
      sha256: "abc",
      kind: "text",
      truncated: false,
      size: 13,
    });
    render(<FileEditor workspaceId="ws1" path="src/a.ts" />);
    const editor = await screen.findByTestId("monaco-editor");
    expect((editor as HTMLTextAreaElement).value).toBe("const x = 1;");
  });

  it("edita o conteúdo (onChange) e habilita o botão salvar (indicador dirty)", async () => {
    fetchFile.mockResolvedValue({
      content: "const x = 1;",
      sha256: "abc",
      kind: "text",
      truncated: false,
      size: 13,
    });
    render(<FileEditor workspaceId="ws1" path="src/a.ts" />);
    const editor = await screen.findByTestId("monaco-editor");
    fireEvent.change(editor, { target: { value: "const x = 2;" } });
    expect(await screen.findByTitle("workbench_files_unsaved")).toBeTruthy();
  });

  it("aplica `monacoFontSize` do settings-store nas options do Monaco", async () => {
    fetchFile.mockResolvedValue({
      content: "x",
      sha256: "abc",
      kind: "text",
      truncated: false,
      size: 1,
    });
    mockSettings.monacoFontSize = 18;
    render(<FileEditor workspaceId="ws1" path="src/a.ts" />);
    const editor = await screen.findByTestId("monaco-editor");
    expect(editor.getAttribute("data-font-size")).toBe("18");
    mockSettings.monacoFontSize = 13;
  });

  it("habilita zoom do Monaco por Ctrl/Cmd + roda do mouse", async () => {
    fetchFile.mockResolvedValue({
      content: "x",
      sha256: "abc",
      kind: "text",
      truncated: false,
      size: 1,
    });
    render(<FileEditor workspaceId="ws1" path="src/a.ts" />);
    await screen.findByTestId("monaco-editor");
    expect(
      screen.getByTestId("monaco-editor").getAttribute("data-mouse-wheel-zoom"),
    ).toBe("true");
  });

  it("arquivo binário delega para o FileViewer em vez de montar o Monaco", async () => {
    fetchFile.mockResolvedValue({
      content: undefined,
      sha256: null,
      kind: "binary",
      truncated: false,
      size: 999,
    });
    render(<FileEditor workspaceId="ws1" path="src/blob.bin" />);
    expect(await screen.findByTestId("file-viewer-fallback")).toBeTruthy();
    expect(screen.queryByTestId("monaco-editor")).toBeNull();
  });

  it("arquivo de mídia (extensão .png) delega para FileViewer sem chamar fetchFile", async () => {
    render(<FileEditor workspaceId="ws1" path="assets/logo.png" />);
    expect(await screen.findByTestId("file-viewer-fallback")).toBeTruthy();
    expect(fetchFile).not.toHaveBeenCalled();
  });

  it("erro/ausência de conteúdo (fetchFile resolve null) não quebra e mantém editor vazio", async () => {
    fetchFile.mockResolvedValue(null);
    render(<FileEditor workspaceId="ws1" path="src/missing.ts" />);
    const editor = await screen.findByTestId("monaco-editor");
    expect((editor as HTMLTextAreaElement).value).toBe("");
  });
});
