// @vitest-environment jsdom
/**
 * FileItem — comportamento de abertura de arquivo em modo IDE vs Assistente.
 *
 * Em uiMode='ide': clique chama openDocked.
 * Em uiMode='assistant': clique abre um documento no canvas compartilhado.
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

const mockOpenWindow = vi.fn();
const mockOpenDocked = vi.fn();
const mockOpenCanvasDocument = vi.fn();
const mockTogglePinned = vi.fn();
const mockSettings = { uiMode: "assistant" };

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
      isPinned: () => boolean;
      togglePinned: typeof mockTogglePinned;
      getFiles: () => { openPath: null };
    }) => unknown,
  ) =>
    sel({
      isPinned: () => false,
      togglePinned: mockTogglePinned,
      getFiles: () => ({ openPath: null }),
    }),
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_t, prop) => () => String(prop) }),
}));

vi.mock("@/components/icons/file-icon", () => ({
  FileIcon: () => null,
}));

vi.mock("../git-badge", () => ({
  GitBadge: () => null,
}));

vi.stubGlobal(
  "fetch",
  vi.fn(() => Promise.resolve(new Response("{}", { status: 200 }))),
);

import { FileItem } from "../file-item";

const ENTRY = {
  name: "main.ts",
  path: "src/main.ts",
  kind: "file" as const,
  size: 100,
};

function renderItem(uiMode: "assistant" | "ide" | "kanban" = "assistant") {
  mockSettings.uiMode = uiMode;
  render(
    <FileItem
      threadId="t1"
      workspaceId="ws1"
      entry={ENTRY}
      depth={0}
      onOpenFile={vi.fn()}
      onDelete={vi.fn()}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockSettings.uiMode = "assistant";
});

beforeEach(() => {
  mockSettings.uiMode = "assistant";
});

describe("FileItem — abertura em modo IDE vs Assistente", () => {
  it("uiMode='assistant': clicar no arquivo abre documento no canvas", () => {
    renderItem("assistant");
    fireEvent.click(screen.getByText("main.ts"));
    expect(mockOpenCanvasDocument).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "file:ws1:src/main.ts",
        kind: "file",
        workspaceId: "ws1",
        threadId: "t1",
        path: "src/main.ts",
      }),
    );
    expect(mockOpenDocked).not.toHaveBeenCalled();
  });

  it("uiMode='ide': clicar no arquivo chama openDocked", () => {
    renderItem("ide");
    fireEvent.click(screen.getByText("main.ts"));
    expect(mockOpenDocked).toHaveBeenCalledWith("ws1", "src/main.ts");
    expect(mockOpenWindow).not.toHaveBeenCalled();
  });

  it("uiMode='assistant': descritor recebe workspace, thread e path", () => {
    renderItem("assistant");
    fireEvent.click(screen.getByText("main.ts"));
    expect(mockOpenCanvasDocument).toHaveBeenCalledOnce();
    expect(mockOpenCanvasDocument.mock.calls[0][0]).toMatchObject({
      workspaceId: "ws1",
      threadId: "t1",
      path: "src/main.ts",
    });
  });

  it("uiMode='ide': openDocked recebe o workspaceId e path corretos", () => {
    renderItem("ide");
    fireEvent.click(screen.getByText("main.ts"));
    expect(mockOpenDocked).toHaveBeenCalledOnce();
    const [wsId, path] = mockOpenDocked.mock.calls[0];
    expect(wsId).toBe("ws1");
    expect(path).toBe("src/main.ts");
  });

  it("renderiza o nome do arquivo no botão", () => {
    renderItem("assistant");
    expect(screen.getByText("main.ts")).toBeInTheDocument();
  });

  it("renderiza com role=treeitem", () => {
    renderItem("assistant");
    expect(document.querySelector("[role='treeitem']")).not.toBeNull();
  });
});

describe("FileItem — drag-and-drop", () => {
  it("é arrastável e grava o próprio path no dataTransfer com o MIME da árvore", () => {
    renderItem("assistant");
    const node = document.querySelector("[role='treeitem']")!;
    expect(node).toHaveAttribute("draggable", "true");

    const setData = vi.fn();
    fireEvent.dragStart(node, {
      dataTransfer: { setData, effectAllowed: "" },
    });
    expect(setData).toHaveBeenCalledWith(
      "application/x-vectora-fs-path",
      "src/main.ts",
    );
  });
});
