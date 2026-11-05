// @vitest-environment jsdom
/**
 * FilesTab — busca em conteúdo (toggle, debounce, resultados, sem-resultados)
 * e criação inline de arquivo na raiz da árvore.
 *
 * Complementa files-tab-inline-editor.test.tsx, que já cobre o editor inline
 * e o botão "abrir como janela".
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
  MarkdownView: () => null,
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

// ── util ─────────────────────────────────────────────────────────────────

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
  apiFsSearchMock.mockReset();
  apiFsCreateMock.mockClear();
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

describe("FilesTab — busca em conteúdo", () => {
  it("toggle de busca alterna o placeholder do filtro para o de busca", async () => {
    await act(async () => {
      await renderFilesTab();
    });
    const toggle = document.querySelector(
      "[aria-label='tooltip_files_search']",
    ) as HTMLElement;
    await act(async () => {
      fireEvent.click(toggle);
    });
    expect(
      screen.getByPlaceholderText("workbench_files_search_placeholder"),
    ).toBeInTheDocument();
  });

  it("query com menos de 2 caracteres não dispara busca", async () => {
    await act(async () => {
      await renderFilesTab();
    });
    const toggle = document.querySelector(
      "[aria-label='tooltip_files_search']",
    ) as HTMLElement;
    await act(async () => {
      fireEvent.click(toggle);
    });
    const input = screen.getByPlaceholderText(
      "workbench_files_search_placeholder",
    );
    await act(async () => {
      fireEvent.change(input, { target: { value: "a" } });
      vi.advanceTimersByTime(400);
    });
    expect(apiFsSearchMock).not.toHaveBeenCalled();
  });

  it("query >=2 chars dispara busca após debounce de 350ms e agrupa hits por arquivo", async () => {
    apiFsSearchMock.mockResolvedValue({
      hits: [
        { path: "a.ts", line_number: 1, line_text: "foo" },
        { path: "a.ts", line_number: 2, line_text: "foo bar" },
      ],
      truncated: false,
    });
    await act(async () => {
      await renderFilesTab();
    });
    const toggle = document.querySelector(
      "[aria-label='tooltip_files_search']",
    ) as HTMLElement;
    await act(async () => {
      fireEvent.click(toggle);
    });
    const input = screen.getByPlaceholderText(
      "workbench_files_search_placeholder",
    );
    await act(async () => {
      fireEvent.change(input, { target: { value: "foo" } });
      vi.advanceTimersByTime(350);
    });
    expect(apiFsSearchMock).toHaveBeenCalledWith("ws1", "foo");
    expect(screen.getByText("a.ts")).toBeInTheDocument();
  });

  it("busca sem resultados mostra a mensagem de nenhum resultado", async () => {
    apiFsSearchMock.mockResolvedValue({ hits: [], truncated: false });
    await act(async () => {
      await renderFilesTab();
    });
    const toggle = document.querySelector(
      "[aria-label='tooltip_files_search']",
    ) as HTMLElement;
    await act(async () => {
      fireEvent.click(toggle);
    });
    const input = screen.getByPlaceholderText(
      "workbench_files_search_placeholder",
    );
    await act(async () => {
      fireEvent.change(input, { target: { value: "foo" } });
      vi.advanceTimersByTime(350);
    });
    expect(
      screen.getByText("workbench_files_search_no_results"),
    ).toBeInTheDocument();
  });

  it("fechar a busca (toggle novamente) limpa query e resultados", async () => {
    apiFsSearchMock.mockResolvedValue({
      hits: [{ path: "a.ts", line_number: 1, line_text: "foo" }],
      truncated: false,
    });
    await act(async () => {
      await renderFilesTab();
    });
    const toggle = document.querySelector(
      "[aria-label='tooltip_files_search']",
    ) as HTMLElement;
    await act(async () => {
      fireEvent.click(toggle);
    });
    const input = screen.getByPlaceholderText(
      "workbench_files_search_placeholder",
    );
    await act(async () => {
      fireEvent.change(input, { target: { value: "foo" } });
      vi.advanceTimersByTime(350);
    });
    expect(screen.getByText("a.ts")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(toggle);
    });
    expect(screen.queryByText("a.ts")).toBeNull();
    expect(
      screen.queryByPlaceholderText("workbench_files_search_placeholder"),
    ).toBeNull();
  });
});

describe("FilesTab — criação inline na raiz", () => {
  it("botão novo arquivo abre o InlineCreateInput no diretório raiz", async () => {
    await act(async () => {
      await renderFilesTab();
    });
    const btn = document.querySelector(
      "[aria-label='tooltip_files_new_file']",
    ) as HTMLElement;
    await act(async () => {
      fireEvent.click(btn);
    });
    expect(
      screen.getByPlaceholderText("workbench_files_creating_file"),
    ).toBeInTheDocument();
  });

  it("confirmar criação (Enter) chama apiFsCreate com o path relativo à raiz", async () => {
    await act(async () => {
      await renderFilesTab();
    });
    const btn = document.querySelector(
      "[aria-label='tooltip_files_new_file']",
    ) as HTMLElement;
    await act(async () => {
      fireEvent.click(btn);
    });
    const input = screen.getByPlaceholderText("workbench_files_creating_file");
    await act(async () => {
      fireEvent.change(input, { target: { value: "novo.ts" } });
      fireEvent.keyDown(input, { key: "Enter" });
    });
    expect(apiFsCreateMock).toHaveBeenCalledWith("ws1", "file", "novo.ts");
  });

  it("Escape cancela a criação e remove o input sem chamar apiFsCreate", async () => {
    await act(async () => {
      await renderFilesTab();
    });
    const btn = document.querySelector(
      "[aria-label='tooltip_files_new_file']",
    ) as HTMLElement;
    await act(async () => {
      fireEvent.click(btn);
    });
    const input = screen.getByPlaceholderText("workbench_files_creating_file");
    await act(async () => {
      fireEvent.keyDown(input, { key: "Escape" });
    });
    expect(
      screen.queryByPlaceholderText("workbench_files_creating_file"),
    ).toBeNull();
    expect(apiFsCreateMock).not.toHaveBeenCalled();
  });
});
