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
      lineHeight?: number;
      mouseWheelZoom?: boolean;
    };
  }) => (
    <textarea
      data-testid="monaco-editor"
      data-readonly={String(!!options?.readOnly)}
      data-font-size={options?.fontSize}
      data-line-height={options?.lineHeight}
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

const mockSettings = {
  monacoFontSize: 13,
  editorFileWatcherEnabled: false,
  editorEncoding: "utf8",
  editorEndOfLine: "lf",
};
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
import {
  editorBuffers,
  editorKey,
  useEditorRegistry,
} from "@/lib/stores/editor-registry";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  editorBuffers.clear();
  vi.useRealTimers();
  mockSettings.editorFileWatcherEnabled = false;
  mockSettings.editorEncoding = "utf8";
  mockSettings.editorEndOfLine = "lf";
});

describe("FileEditor", () => {
  it("ignores a watcher response arriving after local edits", async () => {
    mockSettings.editorFileWatcherEnabled = true;
    fetchFile.mockResolvedValueOnce({
      content: "original",
      sha256: "one",
      kind: "text",
    });
    render(<FileEditor workspaceId="watch" path="file.ts" />);
    const editor = await screen.findByTestId("monaco-editor");
    let resolve!: (value: unknown) => void;
    fetchFile.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    vi.useFakeTimers();
    // Recreate the interval under fake timers.
    fireEvent.change(editor, { target: { value: "temporary" } });
    fireEvent.change(editor, { target: { value: "original" } });
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    fireEvent.change(editor, { target: { value: "unsaved" } });
    await act(async () => {
      resolve({ content: "external", sha256: "two", kind: "text" });
    });
    expect((editor as HTMLTextAreaElement).value).toBe("unsaved");
  });

  it("does not apply a watcher response after an edit is reverted", async () => {
    mockSettings.editorFileWatcherEnabled = true;
    fetchFile.mockResolvedValueOnce({
      content: "original",
      sha256: "one",
      kind: "text",
    });
    render(<FileEditor workspaceId="watch-revert" path="file.ts" />);
    const editor = await screen.findByTestId("monaco-editor");
    vi.useFakeTimers();
    fireEvent.change(editor, { target: { value: "temporary" } });
    fireEvent.change(editor, { target: { value: "original" } });
    let resolve!: (value: unknown) => void;
    fetchFile.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });

    fireEvent.change(editor, { target: { value: "new local edit" } });
    fireEvent.change(editor, { target: { value: "original" } });
    await act(async () => {
      resolve({ content: "external", sha256: "two", kind: "text" });
    });

    expect((editor as HTMLTextAreaElement).value).toBe("original");
  });

  it("serializes BOM and CRLF only in the save payload", async () => {
    mockSettings.editorEncoding = "utf8bom";
    mockSettings.editorEndOfLine = "crlf";
    fetchFile.mockResolvedValue({
      content: "old",
      sha256: "one",
      kind: "text",
    });
    apiUpdateFile.mockResolvedValue({ ok: true, sha256: "two" });
    render(<FileEditor workspaceId="save" path="file.ts" />);
    const editor = await screen.findByTestId("monaco-editor");
    fireEvent.change(editor, { target: { value: "first\nsecond" } });
    await act(async () => {
      await useEditorRegistry
        .getState()
        .entries[editorKey("save", "file.ts")].save();
    });
    expect(apiUpdateFile).toHaveBeenCalledWith(
      "save",
      "file.ts",
      "\ufefffirst\r\nsecond",
      "one",
    );
    expect((editor as HTMLTextAreaElement).value).toBe("first\nsecond");
    expect(screen.queryByTitle("workbench_files_unsaved")).toBeNull();
  });
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
    expect(
      screen.getByTestId("monaco-editor").getAttribute("data-line-height"),
    ).toBe("22");
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
