// @vitest-environment jsdom
/**
 * GitTab — a escolha do repositório: o seletor aparece também quando a raiz não é
 * um repositório, e trocar de repositório recarrega o painel.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, act } from "@testing-library/react";
import { useWorkbenchStore } from "@/lib/stores/workbench-store";

const api = vi.hoisted(() => ({
  fetchGitRepos: vi.fn(),
  selectGitRepo: vi.fn(),
  fetchDiff: vi.fn(),
  fetchGitStatus: vi.fn(),
  fetchGitOperation: vi.fn(),
  fetchBranches: vi.fn(),
  fetchPullRequests: vi.fn(),
  apiCreatePR: vi.fn(),
}));
vi.mock("../api", () => api);

const mounts = vi.hoisted(() => ({ changes: 0, history: 0 }));
vi.mock("../changes-view", async () => {
  const { useEffect } = await import("react");
  return {
    ChangesView: () => {
      useEffect(() => {
        mounts.changes += 1;
      }, []);
      return <div data-testid="changes" />;
    },
  };
});
vi.mock("../history-view", () => ({
  HistoryView: () => <div data-testid="history" />,
}));
vi.mock("../compare-view", () => ({ CompareView: () => null }));
vi.mock("../git-toolbar", () => ({
  GitToolbar: () => <div data-testid="toolbar" />,
}));
vi.mock("../stash-modal", () => ({ StashModal: () => null }));
vi.mock("../worktrees-modal", () => ({ WorktreesModal: () => null }));
vi.mock("../../tabs/git-skeleton", () => ({ GitSkeleton: () => null }));
vi.mock("@/components/workbench/settings/workbench-settings-surface", () => ({
  WorkbenchSettingsSurface: () => null,
}));
vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (selector: (state: unknown) => unknown) =>
    selector({ getActive: () => ({ id: "ws1" }) }),
}));
vi.mock("@/lib/stores/ci-store", () => ({
  useCIStore: (selector: (state: { lastRun: null }) => unknown) =>
    selector({ lastRun: null }),
}));
vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: { getState: () => ({ error: vi.fn() }) },
}));
vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
  }: {
    value?: string;
    onValueChange: (value: string) => void;
    children: React.ReactNode;
  }) => (
    <select
      aria-label="seletor"
      value={value ?? ""}
      onChange={(event) => onValueChange(event.target.value)}
    >
      <option value="" disabled />
      {children}
    </select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  SelectItem: ({
    value,
    children,
  }: {
    value: string;
    children: React.ReactNode;
  }) => <option value={value}>{children}</option>,
}));
vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_target, key) => () => String(key) }),
}));

import { GitTab } from "../git-tab";

const repoA = { path: "api", name: "api", kind: "repo" as const };
const repoB = { path: "web", name: "web", kind: "repo" as const };
const summary = (isGit: boolean) => ({ is_git_repo: isGit, files: [] });

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
  mounts.changes = 0;
  api.fetchGitStatus.mockResolvedValue(null);
  api.fetchBranches.mockResolvedValue(null);
  api.fetchGitOperation.mockResolvedValue(null);
  api.fetchPullRequests.mockResolvedValue({ available: false, prs: [] });
  useWorkbenchStore.setState({ git: {} } as never);
});
afterEach(cleanup);

describe("GitTab — escolha do repositório", () => {
  it("numa pasta que não é repositório mas contém repositórios, oferece escolher", async () => {
    api.fetchDiff.mockResolvedValue(summary(false));
    api.fetchGitRepos.mockResolvedValue({
      repos: [repoA, repoB],
      selected: "",
    });

    render(<GitTab threadId="t1" />);

    expect(await screen.findByTestId("git-repo-selector")).toBeTruthy();
    expect(screen.getByText("workbench_diff_not_git")).toBeTruthy();
    expect(screen.queryByTestId("toolbar")).toBeNull();
  });

  it("numa pasta sem repositório algum, só diz que não é git", async () => {
    api.fetchDiff.mockResolvedValue(summary(false));
    api.fetchGitRepos.mockResolvedValue({ repos: [], selected: "" });

    render(<GitTab threadId="t1" />);

    expect(await screen.findByText("workbench_diff_not_git")).toBeTruthy();
    await waitFor(() => expect(api.fetchGitRepos).toHaveBeenCalled());
    expect(screen.queryByTestId("git-repo-selector")).toBeNull();
  });

  it("escolher um repositório recarrega o painel e a pasta passa a ser tratada como git", async () => {
    api.fetchDiff
      .mockResolvedValueOnce(summary(false))
      .mockResolvedValue(summary(true));
    api.fetchGitRepos.mockResolvedValue({
      repos: [repoA, repoB],
      selected: "",
    });
    api.selectGitRepo.mockResolvedValue({
      repos: [repoA, repoB],
      selected: "web",
    });
    render(<GitTab threadId="t1" />);
    const picker = (await screen.findByLabelText(
      "seletor",
    )) as HTMLSelectElement;

    await act(async () => {
      picker.value = "web";
      picker.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(await screen.findByTestId("toolbar")).toBeTruthy();
    expect(screen.getByTestId("git-repo-selector")).toBeTruthy();
    expect(api.selectGitRepo).toHaveBeenCalledWith("ws1", "web");
    expect(api.fetchGitStatus.mock.calls.length).toBeGreaterThan(0);
  });

  it("trocar de repositório remonta as visões, para não mostrar dados do anterior", async () => {
    api.fetchDiff.mockResolvedValue(summary(true));
    api.fetchGitRepos.mockResolvedValue({
      repos: [repoA, repoB],
      selected: "api",
    });
    api.selectGitRepo.mockResolvedValue({
      repos: [repoA, repoB],
      selected: "web",
    });
    render(<GitTab threadId="t1" />);
    const picker = (await screen.findByLabelText(
      "seletor",
    )) as HTMLSelectElement;
    await screen.findByTestId("changes");
    expect(mounts.changes).toBe(1);

    await act(async () => {
      picker.value = "web";
      picker.dispatchEvent(new Event("change", { bubbles: true }));
    });

    await waitFor(() => expect(mounts.changes).toBe(2));
  });
});
