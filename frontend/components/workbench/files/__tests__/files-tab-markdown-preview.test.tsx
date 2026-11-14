// @vitest-environment jsdom
/**
 * FilesTab — prévia ampliada de markdown: o arquivo .md aberto ganha um botão
 * que abre o diálogo redimensionável com o mesmo conteúdo.
 */

import { describe, expect, it, afterEach, beforeEach, vi } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  act,
} from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.stubGlobal(
  "fetch",
  vi.fn(async () => new Response(JSON.stringify({}), { status: 200 })),
);

// ── mocks de stores ───────────────────────────────────────────────────────

const mockWorkbench = {
  getFiles: (_: string) => ({
    openPath: null,
    contents: {},
    filter: "",
    entriesByDir: {
      "": [] as { name: string; path: string; kind: "file" | "dir" }[],
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
  invalidateFiles: vi.fn(),
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
  useToastStore: { getState: () => ({ success: vi.fn(), error: vi.fn() }) },
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
  MarkdownView: ({ content }: { content: string }) => (
    <div data-testid="markdown-view">{content}</div>
  ),
}));

vi.mock("@/components/workbench/tabs/file-tree-skeleton", () => ({
  FileTreeSkeleton: () => null,
}));

vi.mock("@/components/ui/confirm-dialog", () => ({
  ConfirmDialog: () => null,
}));

const apiFsSearchMock = vi.fn();
const apiFsCreateMock = vi.fn(async (..._args: unknown[]) => true);

vi.mock("../files-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../files-api")>();
  return {
    ...actual,
    apiFsSearch: (...args: Parameters<typeof actual.apiFsSearch>) =>
      apiFsSearchMock(...args),
    apiFsCreate: (...args: Parameters<typeof actual.apiFsCreate>) =>
      apiFsCreateMock(...args),
    fetchDiffSummary: vi.fn(async () => null),
  };
});

function openFile(path: string, content: string) {
  mockWorkbench.getFiles = ((_: string) => ({
    openPath: path,
    contents: { [path]: { path, kind: "text", content, size: content.length } },
    filter: "",
    entriesByDir: { "": [] },
    expandedDirs: [],
    fetchedAt: {},
  })) as unknown as typeof mockWorkbench.getFiles;
}

async function renderFilesTab() {
  const { FilesTab } = await import("../files-tab");
  return render(
    <TooltipProvider>
      <FilesTab threadId="t1" />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  cleanup();
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
  mockWorkbench.getFiles = (_: string) => ({
    openPath: null,
    contents: {},
    filter: "",
    entriesByDir: { "": [] },
    expandedDirs: [],
    fetchedAt: {},
  });
});

describe("FilesTab — prévia ampliada de markdown", () => {
  it("arquivo markdown aberto mostra o botão e ele abre o diálogo com o conteúdo", async () => {
    openFile("docs/README.md", "# Título do documento");
    await act(async () => {
      await renderFilesTab();
    });
    expect(screen.queryByRole("dialog")).toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByText("workbench_preview_md_expand"));
    });

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("docs/README.md");
    expect(dialog).toHaveTextContent("# Título do documento");
  });

  it("fechar o diálogo volta ao arquivo", async () => {
    openFile("README.md", "# Oi");
    await act(async () => {
      await renderFilesTab();
    });
    await act(async () => {
      fireEvent.click(screen.getByText("workbench_preview_md_expand"));
    });
    await screen.findByRole("dialog");

    await act(async () => {
      fireEvent.click(screen.getByText("dialog_close").closest("button")!);
    });

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("workbench_preview_md_expand")).toBeInTheDocument();
  });

  it("arquivo que não é markdown não oferece a prévia ampliada", async () => {
    openFile("src/main.ts", "export {}");
    await act(async () => {
      await renderFilesTab();
    });

    expect(screen.queryByText("workbench_preview_md_expand")).toBeNull();
  });
});
