// @vitest-environment jsdom
/**
 * PinnedSection — abertura de arquivo fixado em modo IDE vs Assistente.
 *
 * Em uiMode='ide': clique chama openDocked.
 * Em uiMode='assistant': clique abre documento no canvas compartilhado.
 * Sem pins: não renderiza nada.
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

const mockOpenWindow = vi.fn();
const mockOpenDocked = vi.fn();
const mockOpenCanvasDocument = vi.fn();
const mockTogglePinned = vi.fn();
const mockSettings = { uiMode: "assistant" };

const mockPinnedFiles: Record<string, string[]> = {};

vi.mock("@/lib/stores/windows-store", () => ({
  useWindowsStore: (
    sel: (s: {
      open: typeof mockOpenWindow;
      openDocked: typeof mockOpenDocked;
      openCanvasDocument: typeof mockOpenCanvasDocument;
    }) => unknown,
  ) =>
    sel({
      open: mockOpenWindow,
      openDocked: mockOpenDocked,
      openCanvasDocument: mockOpenCanvasDocument,
    }),
}));

vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (sel: (s: typeof mockSettings) => unknown) =>
    sel(mockSettings),
}));

vi.mock("@/lib/stores/workbench-store", () => ({
  useWorkbenchStore: (
    sel: (s: {
      pinnedFiles: typeof mockPinnedFiles;
      togglePinned: typeof mockTogglePinned;
    }) => unknown,
  ) => sel({ pinnedFiles: mockPinnedFiles, togglePinned: mockTogglePinned }),
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_t, prop) => () => String(prop) }),
}));

import { PinnedSection } from "../pinned-section";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockSettings.uiMode = "assistant";
  delete mockPinnedFiles["t1"];
});

beforeEach(() => {
  mockSettings.uiMode = "assistant";
  delete mockPinnedFiles["t1"];
});

describe("PinnedSection — abertura em modo IDE vs Assistente", () => {
  it("não renderiza nada quando não há arquivos fixados", () => {
    const { container } = render(
      <PinnedSection threadId="t1" workspaceId="ws1" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("uiMode='assistant': clicar em pin abre documento no canvas", () => {
    mockPinnedFiles["t1"] = ["src/important.ts"];
    render(<PinnedSection threadId="t1" workspaceId="ws1" />);
    fireEvent.click(screen.getByText("important.ts"));
    expect(mockOpenCanvasDocument).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "file:ws1:src/important.ts",
        kind: "file",
        workspaceId: "ws1",
        threadId: "t1",
        path: "src/important.ts",
      }),
    );
    expect(mockOpenDocked).not.toHaveBeenCalled();
  });

  it("uiMode='ide': clicar em pin chama openDocked", () => {
    mockSettings.uiMode = "ide";
    mockPinnedFiles["t1"] = ["src/important.ts"];
    render(<PinnedSection threadId="t1" workspaceId="ws1" />);
    fireEvent.click(screen.getByText("important.ts"));
    expect(mockOpenDocked).toHaveBeenCalledWith("ws1", "src/important.ts");
    expect(mockOpenWindow).not.toHaveBeenCalled();
  });

  it("renderiza o nome do arquivo (basename) do pin", () => {
    mockPinnedFiles["t1"] = ["src/nested/utils.ts"];
    render(<PinnedSection threadId="t1" workspaceId="ws1" />);
    expect(screen.getByText("utils.ts")).toBeInTheDocument();
  });

  it("múltiplos pins renderizados; cada um usa o modo correto", () => {
    mockPinnedFiles["t1"] = ["a/foo.ts", "b/bar.ts"];
    render(<PinnedSection threadId="t1" workspaceId="ws1" />);
    fireEvent.click(screen.getByText("foo.ts"));
    expect(mockOpenCanvasDocument).toHaveBeenCalledWith(
      expect.objectContaining({ path: "a/foo.ts" }),
    );
    vi.clearAllMocks();
    fireEvent.click(screen.getByText("bar.ts"));
    expect(mockOpenCanvasDocument).toHaveBeenCalledWith(
      expect.objectContaining({ path: "b/bar.ts" }),
    );
  });
});
