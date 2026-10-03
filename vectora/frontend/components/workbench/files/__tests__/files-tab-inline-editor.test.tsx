// @vitest-environment jsdom
/**
 * Inline editor no viewer de arquivos.
 *
 * Testa a lógica de toggle edit/cancel e preenchimento do textarea.
 * Usa globalThis.fetch mockado para isolar chamadas de rede.
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

// ── fetch global mockado para evitar chamadas reais ──────────────────────────
const FILE_CONTENT = "const x = 1;\n";
const FETCH_MOCK = vi.fn(async (url: string) => {
  const urlStr = String(url);
  if (urlStr.includes("/file")) {
    return new Response(
      JSON.stringify({
        content: FILE_CONTENT,
        sha256: "abc123",
        binary: false,
        truncated: false,
        kind: "text",
        size: FILE_CONTENT.length,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }
  if (urlStr.includes("/git/diff")) {
    return new Response(
      JSON.stringify({
        is_git_repo: false,
        files: [],
        total_additions: 0,
        total_deletions: 0,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }
  return new Response(JSON.stringify([]), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
});

beforeEach(() => {
  vi.stubGlobal("fetch", FETCH_MOCK);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ── mocks de stores ───────────────────────────────────────────────────────────

const OPEN_FILE = "src/hello.ts";

const mockWorkbench = {
  getFiles: (_: string) => ({
    openPath: OPEN_FILE,
    contents: {
      [OPEN_FILE]: {
        content: FILE_CONTENT,
        sha256: "abc123",
        binary: false,
        truncated: false,
        kind: "text",
        size: FILE_CONTENT.length,
      },
    },
    filter: "",
    entriesByDir: { "": [] },
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

const mockFilesTabSettings = { uiMode: "assistant" };

vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (sel: (s: typeof mockFilesTabSettings) => unknown) =>
    sel(mockFilesTabSettings),
}));

vi.mock("@/lib/stores/windows-store", () => ({
  useWindowsStore: (
    sel: (s: { open: () => void; openDocked: () => void }) => unknown,
  ) => sel({ open: vi.fn(), openDocked: vi.fn() }),
}));

vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: {
    getState: () => ({ success: vi.fn(), error: vi.fn() }),
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

// ── testes ───────────────────────────────────────────────────────────────────

async function renderFilesTab() {
  const { FilesTab } = await import("../files-tab");
  return render(
    <TooltipProvider>
      <FilesTab threadId="t1" />
    </TooltipProvider>,
  );
}

afterEach(() => {
  mockFilesTabSettings.uiMode = "assistant";
});

describe("FilesTab — botão abrir como janela por modo de interface", () => {
  it("uiMode='assistant': botão 'abrir como janela' está no DOM quando há arquivo aberto", async () => {
    mockFilesTabSettings.uiMode = "assistant";
    await act(async () => {
      await renderFilesTab();
    });
    const btn = document.querySelector("[aria-label='window_open_as_window']");
    expect(btn).not.toBeNull();
  });

  it("uiMode='ide': botão 'abrir como janela' não está no DOM", async () => {
    mockFilesTabSettings.uiMode = "ide";
    await act(async () => {
      await renderFilesTab();
    });
    const btn = document.querySelector("[aria-label='window_open_as_window']");
    expect(btn).toBeNull();
  });
});

describe("FilesTab inline editor", () => {
  it("botão Pencil com data-editing=false está presente no viewer", async () => {
    await act(async () => {
      await renderFilesTab();
    });
    const btn = document.querySelector("[data-editing='false']");
    expect(btn).not.toBeNull();
  });

  it("clicar no Pencil exibe textarea inline-editor-textarea", async () => {
    await act(async () => {
      await renderFilesTab();
    });
    const btn = document.querySelector("[data-editing='false']") as HTMLElement;
    await act(async () => {
      fireEvent.click(btn);
    });
    expect(
      document.querySelector("[data-testid='inline-editor-textarea']"),
    ).not.toBeNull();
  });

  it("textarea contém o conteúdo do arquivo ao entrar em edição", async () => {
    await act(async () => {
      await renderFilesTab();
    });
    const btn = document.querySelector("[data-editing='false']") as HTMLElement;
    await act(async () => {
      fireEvent.click(btn);
    });
    const textarea = document.querySelector(
      "[data-testid='inline-editor-textarea']",
    ) as HTMLTextAreaElement;
    expect(textarea?.value).toBe(FILE_CONTENT);
  });

  it("clicar no Pencil de novo (data-editing=true) volta para view", async () => {
    await act(async () => {
      await renderFilesTab();
    });
    const btnEdit = document.querySelector(
      "[data-editing='false']",
    ) as HTMLElement;
    await act(async () => {
      fireEvent.click(btnEdit);
    });
    const btnCancel = document.querySelector(
      "[data-editing='true']",
    ) as HTMLElement;
    expect(btnCancel).not.toBeNull();
    await act(async () => {
      fireEvent.click(btnCancel);
    });
    expect(
      document.querySelector("[data-testid='inline-editor-textarea']"),
    ).toBeNull();
  });
});
