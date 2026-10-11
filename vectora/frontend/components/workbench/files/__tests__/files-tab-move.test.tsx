// @vitest-environment jsdom
/**
 * FilesTab — drag-and-drop pra mover arquivo/pasta entre diretórios.
 * Complementa dir-node.test.tsx (contrato do callback
 * `onMoveInto`) testando o handler real (`handleMoveInto`): chama
 * apiFsMove com o destino certo, invalida a árvore em sucesso, mostra
 * toast em erro, e nunca chama a API pra soltar no mesmo lugar ou pra
 * mover uma pasta pra dentro de si mesma.
 */

import { describe, expect, it, afterEach, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.stubGlobal(
  "fetch",
  vi.fn(async () => new Response(JSON.stringify({}), { status: 200 })),
);

const mockToastError = vi.fn();
const mockInvalidateFiles = vi.fn();

const mockWorkbench = {
  getFiles: (_: string) => ({
    openPath: null,
    contents: {},
    filter: "",
    entriesByDir: {
      "": [
        { name: "a.ts", path: "a.ts", kind: "file" as const },
        { name: "sub", path: "sub", kind: "dir" as const },
      ],
    },
    expandedDirs: [] as string[],
    fetchedAt: {} as Record<string, number>,
  }),
  getGit: (_: string) => ({
    summary: null,
    summaryFetchedAt: 0,
    openFiles: [],
    hunksByFile: {},
    fileFetchedAt: {},
  }),
  toggleExpanded: vi.fn(),
  setFilesEntries: vi.fn(),
  setFilesFilter: vi.fn(),
  setOpenFile: vi.fn(),
  setFileContent: vi.fn(),
  invalidateFiles: mockInvalidateFiles,
  viewerHeight: 280,
  setViewerHeight: vi.fn(),
  clearPending: vi.fn(),
  setGitSummary: vi.fn(),
  pinnedFiles: {},
  setPinnedFiles: vi.fn(),
  loadPins: vi.fn(),
  setPins: vi.fn(),
  togglePinned: vi.fn(),
  isPinned: () => false,
  getOpen: () => true,
};

vi.mock("@/lib/stores/workbench-store", () => ({
  WORKBENCH_STALE_MS: 30000,
  WORKBENCH_TABS: [
    "files",
    "diff",
    "plan",
    "tasks",
    "browser",
    "storage",
    "context_graph",
    "library",
    "terminal",
  ],
  useWorkbenchStore: (sel: (s: typeof mockWorkbench) => unknown) =>
    sel(mockWorkbench),
}));

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (sel: (s: { getActive: () => object }) => unknown) =>
    sel({
      getActive: () => ({
        id: "ws1",
        name: "test",
        cwd: "/test",
        is_git_repo: false,
      }),
    }),
}));

const mockSettings = { uiMode: "assistant" };

vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (sel: (s: typeof mockSettings) => unknown) =>
    sel(mockSettings),
}));

vi.mock("@/lib/stores/windows-store", () => ({
  useWindowsStore: (
    sel: (s: { open: () => void; openDocked: () => void }) => unknown,
  ) => sel({ open: vi.fn(), openDocked: vi.fn() }),
}));

vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: {
    getState: () => ({ success: vi.fn(), error: mockToastError }),
  },
}));

vi.mock("@/lib/hooks/workbench/use-swr", () => ({
  useWorkbenchSWR: vi.fn(),
}));

vi.mock("@/lib/hooks/use-delayed-loading", () => ({
  useDelayedLoading: () => false,
}));

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

vi.mock("@/components/workbench/file-viewer", () => ({
  getMediaKind: () => null,
  MediaView: () => null,
}));

vi.mock("@/components/workbench/markdown-view", () => ({
  MarkdownView: () => null,
}));

vi.mock("@/components/workbench/tabs/file-tree-skeleton", () => ({
  FileTreeSkeleton: () => null,
}));

vi.mock("@/components/ui/confirm-dialog", () => ({
  ConfirmDialog: () => null,
}));

const apiFsMoveMock = vi.fn(
  async (..._args: unknown[]) =>
    ({ ok: true }) as { ok: boolean; message?: string },
);

vi.mock("../files-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../files-api")>();
  return {
    ...actual,
    apiFsMove: (...args: Parameters<typeof actual.apiFsMove>) =>
      apiFsMoveMock(...args),
    fetchDiffSummary: vi.fn(async () => null),
  };
});

async function renderFilesTab() {
  const { FilesTab } = await import("../files-tab");
  return render(
    <TooltipProvider>
      <FilesTab threadId="t1" />
    </TooltipProvider>,
  );
}

function dropFsPath(el: Element, sourcePath: string) {
  fireEvent.drop(el, {
    dataTransfer: {
      types: ["application/x-vectora-fs-path"],
      getData: () => sourcePath,
    },
  });
}

beforeEach(() => {
  apiFsMoveMock.mockClear();
  apiFsMoveMock.mockResolvedValue({ ok: true });
  mockInvalidateFiles.mockClear();
  mockToastError.mockClear();
});

afterEach(() => {
  cleanup();
});

describe("FilesTab — mover por drag-and-drop", () => {
  it("soltar um arquivo sobre uma pasta chama apiFsMove com o destino certo e invalida a árvore", async () => {
    await renderFilesTab();
    const target = screen.getByText("sub").closest("[role='treeitem']")!;

    dropFsPath(target, "a.ts");

    await vi.waitFor(() => {
      expect(apiFsMoveMock).toHaveBeenCalledWith("ws1", "a.ts", "sub/a.ts");
    });
    await vi.waitFor(() => {
      expect(mockInvalidateFiles).toHaveBeenCalledWith("ws1");
    });
  });

  it("erro/borda: resposta não-ok mostra toast e nunca invalida a árvore", async () => {
    apiFsMoveMock.mockResolvedValue({
      ok: false,
      message: "Já existe um arquivo ou pasta com esse nome.",
    });
    await renderFilesTab();
    const target = screen.getByText("sub").closest("[role='treeitem']")!;

    dropFsPath(target, "a.ts");

    await vi.waitFor(() => {
      expect(mockToastError).toHaveBeenCalled();
    });
    expect(mockInvalidateFiles).not.toHaveBeenCalled();
  });

  it("erro/borda: soltar a pasta 'sub' sobre si mesma nunca chama apiFsMove", async () => {
    await renderFilesTab();
    const target = screen.getByText("sub").closest("[role='treeitem']")!;

    dropFsPath(target, "sub");

    // Sem await extra: se chamasse, seria síncrono o suficiente pra
    // aparecer aqui — a guarda de auto-referência é o primeiro `if`.
    expect(apiFsMoveMock).not.toHaveBeenCalled();
  });
});
