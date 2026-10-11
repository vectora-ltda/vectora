// @vitest-environment jsdom
/**
 * DirNode — nó de diretório recursivo: expandir/colapsar, carregamento via SWR,
 * filtro de nomes, criação inline e delete via teclado.
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

const mockToggleExpanded = vi.fn();
const mockSetFilesEntries = vi.fn();

interface FilesState {
  expandedDirs: string[];
  entriesByDir: Record<
    string,
    { name: string; path: string; kind: "dir" | "file" }[] | undefined
  >;
  fetchedAt: Record<string, number>;
}

const filesState: FilesState = {
  expandedDirs: [],
  entriesByDir: {},
  fetchedAt: {},
};

vi.mock("@/lib/stores/workbench-store", () => ({
  WORKBENCH_STALE_MS: 30000,
  useWorkbenchStore: (
    sel: (s: {
      getFiles: (id: string) => FilesState;
      toggleExpanded: typeof mockToggleExpanded;
      setFilesEntries: typeof mockSetFilesEntries;
    }) => unknown,
  ) =>
    sel({
      getFiles: () => filesState,
      toggleExpanded: mockToggleExpanded,
      setFilesEntries: mockSetFilesEntries,
    }),
}));

vi.mock("@/lib/hooks/workbench/use-swr", () => ({
  useWorkbenchSWR: vi.fn(),
}));

vi.mock("@/lib/hooks/use-delayed-loading", () => ({
  useDelayedLoading: (v: boolean) => v,
}));

vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: { getState: () => ({ success: vi.fn(), error: vi.fn() }) },
}));

vi.mock("@/components/workbench/tabs/file-tree-skeleton", () => ({
  FileTreeSkeleton: () => <div data-testid="skeleton" />,
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_t, prop) => () => String(prop) }),
}));

vi.mock("../files-api", () => ({
  fetchTree: vi.fn(async () => []),
  apiFsMove: vi.fn(async () => ({ ok: true })),
}));

vi.mock("../file-item", () => ({
  FileItem: ({ entry }: { entry: { name: string; path: string } }) => (
    <div data-testid="file-item">{entry.name}</div>
  ),
}));

import { DirNode } from "../dir-node";

function baseProps() {
  return {
    threadId: "t1",
    workspaceId: "ws1",
    path: "",
    name: "root",
    depth: 0,
    filter: "",
    statusByPath: new Map<string, string>(),
    onOpenFile: vi.fn(),
    onDelete: vi.fn(),
    creating: null,
    onInlineCreate: vi.fn(),
    onCancelCreate: vi.fn(),
    onRequestCreate: vi.fn(),
  };
}

function subDirProps() {
  return { ...baseProps(), path: "sub", name: "sub", depth: 1 };
}

beforeEach(() => {
  filesState.expandedDirs = [];
  filesState.entriesByDir = {};
  filesState.fetchedAt = {};
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DirNode — raiz (depth=0)", () => {
  it("depth=0 é sempre expandido e não renderiza o cabeçalho de pasta (sem treeitem)", () => {
    render(<DirNode {...baseProps()} />);
    expect(document.querySelector("[role='treeitem']")).toBeNull();
  });

  it("sem entries carregadas ainda, mostra o skeleton (aguardando SWR)", () => {
    render(<DirNode {...baseProps()} />);
    expect(screen.getByTestId("skeleton")).toBeInTheDocument();
  });

  it("com entries carregadas, renderiza um FileItem por arquivo e um DirNode filho por pasta", () => {
    filesState.entriesByDir[""] = [
      { name: "a.ts", path: "a.ts", kind: "file" },
      { name: "sub", path: "sub", kind: "dir" },
    ];
    render(<DirNode {...baseProps()} />);
    expect(screen.getByText("a.ts")).toBeInTheDocument();
    expect(screen.getByText("sub")).toBeInTheDocument();
  });

  it("filtro (case-insensitive) esconde entradas que não combinam com o nome", () => {
    filesState.entriesByDir[""] = [
      { name: "Alpha.ts", path: "Alpha.ts", kind: "file" },
      { name: "beta.ts", path: "beta.ts", kind: "file" },
    ];
    render(<DirNode {...baseProps()} filter="alpha" />);
    expect(screen.getByText("Alpha.ts")).toBeInTheDocument();
    expect(screen.queryByText("beta.ts")).toBeNull();
  });

  it("lista de entries vazia: não renderiza FileItem nem skeleton", () => {
    filesState.entriesByDir[""] = [];
    render(<DirNode {...baseProps()} />);
    expect(screen.queryByTestId("file-item")).toBeNull();
    expect(screen.queryByTestId("skeleton")).toBeNull();
  });

  it("creating no diretório atual renderiza o InlineCreateInput com placeholder de arquivo", () => {
    filesState.entriesByDir[""] = [];
    render(
      <DirNode {...baseProps()} creating={{ type: "file", parentDir: "" }} />,
    );
    expect(
      screen.getByPlaceholderText("workbench_files_creating_file"),
    ).toBeInTheDocument();
  });
});

describe("DirNode — subpasta (depth>0)", () => {
  it("renderiza o cabeçalho como treeitem com aria-expanded", () => {
    render(<DirNode {...subDirProps()} />);
    const el = document.querySelector("[role='treeitem']");
    expect(el).not.toBeNull();
    expect(el?.getAttribute("aria-expanded")).toBe("false");
  });

  it("colapsada (não expandida), não busca nem renderiza entries", () => {
    render(<DirNode {...subDirProps()} />);
    expect(screen.queryByTestId("skeleton")).toBeNull();
  });

  it("clicar no nome da pasta chama toggleExpanded com workspaceId e path", () => {
    render(<DirNode {...subDirProps()} />);
    fireEvent.click(screen.getByText("sub"));
    expect(mockToggleExpanded).toHaveBeenCalledWith("ws1", "sub");
  });

  it("expandida via store: mostra spinner de carregamento (sem skeleton, que é só na raiz)", () => {
    filesState.expandedDirs = ["sub"];
    render(<DirNode {...subDirProps()} />);
    expect(document.querySelector(".animate-spin")).not.toBeNull();
  });

  it("pressionar Delete no treeitem chama onDelete com path/name; Shift+Delete marca permanente", () => {
    const onDelete = vi.fn();
    render(<DirNode {...subDirProps()} onDelete={onDelete} />);
    const el = document.querySelector("[role='treeitem']") as HTMLElement;
    fireEvent.keyDown(el, { key: "Delete" });
    expect(onDelete).toHaveBeenCalledWith("sub", "sub", false);
    fireEvent.keyDown(el, { key: "Delete", shiftKey: true });
    expect(onDelete).toHaveBeenLastCalledWith("sub", "sub", true);
  });
});

function dt(data: Record<string, string> = {}) {
  return {
    types: Object.keys(data),
    getData: (k: string) => data[k] ?? "",
    dropEffect: "",
  };
}

describe("DirNode — drag-and-drop", () => {
  it("é arrastável e grava o próprio path no dataTransfer", () => {
    render(<DirNode {...subDirProps()} />);
    const el = document.querySelector("[role='treeitem']")!;
    expect(el).toHaveAttribute("draggable", "true");

    const setData = vi.fn();
    fireEvent.dragStart(el, { dataTransfer: { setData, effectAllowed: "" } });
    expect(setData).toHaveBeenCalledWith(
      "application/x-vectora-fs-path",
      "sub",
    );
  });

  it("soltar um item arrastado chama onMoveInto(sourcePath, path desta pasta)", () => {
    const onMoveInto = vi.fn();
    render(<DirNode {...subDirProps()} onMoveInto={onMoveInto} />);
    const el = document.querySelector("[role='treeitem']")!;

    fireEvent.drop(el, {
      dataTransfer: dt({ "application/x-vectora-fs-path": "other/file.ts" }),
    });
    expect(onMoveInto).toHaveBeenCalledWith("other/file.ts", "sub");
  });

  it("erro/borda: drop sem dado do MIME esperado (drag externo do SO) não chama onMoveInto", () => {
    const onMoveInto = vi.fn();
    render(<DirNode {...subDirProps()} onMoveInto={onMoveInto} />);
    const el = document.querySelector("[role='treeitem']")!;

    fireEvent.drop(el, { dataTransfer: dt({}) });
    expect(onMoveInto).not.toHaveBeenCalled();
  });

  it("dragOver com o MIME certo sinaliza dropEffect='move' (feedback visual)", () => {
    render(<DirNode {...subDirProps()} />);
    const el = document.querySelector("[role='treeitem']")!;
    const dataTransfer = dt({ "application/x-vectora-fs-path": "x" });

    fireEvent.dragOver(el, { dataTransfer });
    expect(dataTransfer.dropEffect).toBe("move");
  });
});
