// @vitest-environment jsdom
/**
 * Testes do GitTab — estados de workspace/summary, badge de CI e troca entre
 * as abas Mudanças/Histórico/Compare. As views filhas (toolbar, mudanças,
 * histórico, compare, modais) são mockadas para isolar a lógica do shell.
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  cleanup,
  waitFor,
} from "@testing-library/react";
import { GitTab } from "../git-tab";
import * as api from "../api";
import type { DiffSummary } from "@/lib/stores/workbench-store";

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

let mockActiveWorkspace: { id: string } | null = { id: "ws1" };
vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (sel: (s: { getActive: () => unknown }) => unknown) =>
    sel({ getActive: () => mockActiveWorkspace }),
}));

let mockLastCi: {
  repo: string;
  name: string;
  status: string;
  conclusion: string | null;
  htmlUrl: string;
  at: number;
} | null = null;
vi.mock("@/lib/stores/ci-store", () => ({
  useCIStore: (sel: (s: { lastRun: typeof mockLastCi }) => unknown) =>
    sel({ lastRun: mockLastCi }),
}));

let mockSummary: DiffSummary | null = null;
const mockWorkbench = {
  getDiff: (_id: string) => ({
    summary: mockSummary,
    summaryFetchedAt: Date.now(),
  }),
  setDiffSummary: vi.fn(),
  invalidateDiff: vi.fn(),
  clearPending: vi.fn(),
  setGitOperation: vi.fn(),
};
vi.mock("@/lib/stores/workbench-store", () => ({
  WORKBENCH_STALE_MS: 30000,
  useWorkbenchStore: (sel: (s: typeof mockWorkbench) => unknown) =>
    sel(mockWorkbench),
}));

vi.mock("@/lib/hooks/workbench/use-swr", () => ({
  useWorkbenchSWR: vi.fn(),
}));
vi.mock("@/lib/hooks/use-delayed-loading", () => ({
  useDelayedLoading: () => false,
}));

vi.mock("../tabs/diff-skeleton", () => ({
  DiffSkeleton: () => <div>skeleton</div>,
}));
vi.mock("../git-toolbar", () => ({
  GitToolbar: () => <div>stub-toolbar</div>,
}));
vi.mock("../changes-view", () => ({
  ChangesView: () => <div>stub-changes</div>,
}));
vi.mock("../history-view", () => ({
  HistoryView: () => <div>stub-history</div>,
}));
vi.mock("../compare-view", () => ({
  CompareView: () => <div>stub-compare</div>,
}));
vi.mock("../stash-modal", () => ({ StashModal: () => null }));
vi.mock("../worktrees-modal", () => ({ WorktreesModal: () => null }));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  mockActiveWorkspace = { id: "ws1" };
  mockLastCi = null;
  mockSummary = null;
});

beforeEach(() => {
  vi.spyOn(api, "fetchGitStatus").mockResolvedValue({
    is_git_repo: true,
    branch: "main",
    clean: true,
    ahead: 0,
    behind: 0,
  });
  vi.spyOn(api, "fetchBranches").mockResolvedValue({
    current: "main",
    branches: ["main"],
    remotes: [],
  });
  vi.spyOn(api, "fetchDiff").mockResolvedValue(null);
  vi.spyOn(api, "fetchGitOperation").mockResolvedValue(null);
});

function repoSummary(files: DiffSummary["files"] = []): DiffSummary {
  return { is_git_repo: true, total_additions: 0, total_deletions: 0, files };
}

describe("GitTab", () => {
  it("mostra mensagem de nenhum workspace quando não há workspace ativo", () => {
    mockActiveWorkspace = null;
    render(<GitTab threadId="t1" />);
    expect(screen.getByText("workbench_diff_no_workspace")).toBeInTheDocument();
  });

  it("mostra a mensagem de repositório não-git quando summary.is_git_repo é false", async () => {
    mockSummary = {
      is_git_repo: false,
      total_additions: 0,
      total_deletions: 0,
      files: [],
    };
    render(<GitTab threadId="t1" />);
    expect(screen.getByText("workbench_diff_not_git")).toBeInTheDocument();
    await waitFor(() => expect(api.fetchGitStatus).toHaveBeenCalled());
  });

  it("renderiza a toolbar e a view de mudanças por padrão quando há um repo git", async () => {
    mockSummary = repoSummary();
    render(<GitTab threadId="t1" />);
    expect(screen.getByText("stub-toolbar")).toBeInTheDocument();
    expect(screen.getByText("stub-changes")).toBeInTheDocument();
    expect(screen.queryByText("stub-history")).not.toBeInTheDocument();
    await waitFor(() => expect(api.fetchGitStatus).toHaveBeenCalled());
  });

  it("alterna para a view de histórico ao clicar na aba Histórico", async () => {
    mockSummary = repoSummary();
    render(<GitTab threadId="t1" />);
    await waitFor(() => expect(api.fetchGitStatus).toHaveBeenCalled());
    fireEvent.click(screen.getByText("workbench_git_tab_history"));
    expect(screen.getByText("stub-history")).toBeInTheDocument();
    expect(screen.queryByText("stub-changes")).not.toBeInTheDocument();
  });

  it("não mostra o badge de CI quando lastRun é null", async () => {
    mockSummary = repoSummary();
    mockLastCi = null;
    render(<GitTab threadId="t1" />);
    expect(screen.queryByTestId("git-ci-badge")).not.toBeInTheDocument();
    await waitFor(() => expect(api.fetchGitStatus).toHaveBeenCalled());
  });

  it("mostra o badge de CI com status em execução quando lastRun.status !== completed", async () => {
    mockSummary = repoSummary();
    mockLastCi = {
      repo: "org/repo",
      name: "build",
      status: "in_progress",
      conclusion: null,
      htmlUrl: "https://github.com/org/repo/actions/runs/1",
      at: Date.now(),
    };
    render(<GitTab threadId="t1" />);
    const badge = screen.getByTestId("git-ci-badge");
    expect(badge).toBeInTheDocument();
    expect(screen.getByText("workbench_ci_running")).toBeInTheDocument();
    await waitFor(() => expect(api.fetchGitStatus).toHaveBeenCalled());
  });

  it("mostra 'falhou' quando o CI completou com conclusion != success", async () => {
    mockSummary = repoSummary();
    mockLastCi = {
      repo: "org/repo",
      name: "build",
      status: "completed",
      conclusion: "failure",
      htmlUrl: "",
      at: Date.now(),
    };
    render(<GitTab threadId="t1" />);
    expect(screen.getByText("workbench_ci_failed")).toBeInTheDocument();
    await waitFor(() => expect(api.fetchGitStatus).toHaveBeenCalled());
  });
});
