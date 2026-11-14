// @vitest-environment jsdom
/**
 * GitStatusBadge: mostra a branch, ahead/behind e se há alterações do
 * repositório que o Git workbench usa, e some quando não há repositório.
 */

import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { GitStatusBadge } from "../git-status-badge";
import {
  useWorkspacesStore,
  type WorkspaceInfo,
} from "@/lib/stores/workspaces-store";

const fetchGitStatus = vi.hoisted(() => vi.fn());
vi.mock("@/components/workbench/git/api", () => ({ fetchGitStatus }));

vi.mock("@/lib/paraglide/messages", () => ({
  m: {
    git_badge_branch: ({ branch }: { branch: string }) => `Branch ${branch}`,
    git_badge_clean: () => "sem alterações",
    git_badge_dirty: () => "alterações não commitadas",
  },
}));

function ws(over: Partial<WorkspaceInfo>): WorkspaceInfo {
  return { id: "w1", name: "w1", cwd: "/x", ...over } as WorkspaceInfo;
}

/** Promessa que o teste resolve na hora que quiser. */
function deferred() {
  const holder: { resolve: (value: unknown) => void } = { resolve: () => {} };
  const promise = new Promise((resolve) => {
    holder.resolve = resolve;
  });
  return { promise, resolve: (value: unknown) => holder.resolve(value) };
}

const status = (over = {}) => ({
  is_git_repo: true,
  branch: "feature-x",
  clean: true,
  ahead: 0,
  behind: 0,
  ...over,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  useWorkspacesStore.setState({
    workspaces: [ws({ id: "w1" })],
    active_id: "w1",
  });
  fetchGitStatus.mockResolvedValue(status());
});

describe("GitStatusBadge", () => {
  it("não renderiza nada sem workspace ativo", () => {
    useWorkspacesStore.setState({ workspaces: [], active_id: null });

    const { container } = render(<GitStatusBadge />);

    expect(container).toBeEmptyDOMElement();
    expect(fetchGitStatus).not.toHaveBeenCalled();
  });

  it("não renderiza nada quando o workspace não tem repositório", async () => {
    fetchGitStatus.mockResolvedValue({ ...status(), is_git_repo: false });

    const { container } = render(<GitStatusBadge />);

    await waitFor(() => expect(fetchGitStatus).toHaveBeenCalledWith("w1"));
    expect(container).toBeEmptyDOMElement();
  });

  it("mostra a branch e o estado limpo", async () => {
    render(<GitStatusBadge />);

    expect(await screen.findByText("feature-x")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "sem alterações" })).toBeTruthy();
    expect(screen.getByTestId("git-status-badge").title).toBe(
      "Branch feature-x · sem alterações",
    );
  });

  it("mostra ahead, behind e alterações não commitadas", async () => {
    fetchGitStatus.mockResolvedValue(
      status({ ahead: 2, behind: 1, clean: false }),
    );

    render(<GitStatusBadge />);

    expect(await screen.findByText("↑2")).toBeInTheDocument();
    expect(screen.getByText("↓1")).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: "alterações não commitadas" }),
    ).toBeTruthy();
    expect(screen.getByTestId("git-status-badge").title).toBe(
      "Branch feature-x · ↑2 · ↓1 · alterações não commitadas",
    );
  });

  it("funciona numa raiz que não é repositório mas contém um escolhido", async () => {
    // O servidor já resolve o repositório em uso: o badge confia na resposta,
    // não no campo `is_git_repo` da raiz do workspace.
    useWorkspacesStore.setState({
      workspaces: [ws({ id: "w1", is_git_repo: false })],
      active_id: "w1",
    });
    fetchGitStatus.mockResolvedValue(status({ branch: "sub-repo" }));

    render(<GitStatusBadge />);

    expect(await screen.findByText("sub-repo")).toBeInTheDocument();
  });

  it("trocar de workspace esconde o status do anterior até chegar o do novo", async () => {
    render(<GitStatusBadge />);
    await screen.findByText("feature-x");

    const pending = deferred();
    fetchGitStatus.mockReturnValue(pending.promise);
    act(() => {
      useWorkspacesStore.setState({
        workspaces: [ws({ id: "w2" })],
        active_id: "w2",
      });
    });

    await waitFor(() => expect(fetchGitStatus).toHaveBeenCalledWith("w2"));
    expect(screen.queryByText("feature-x")).toBeNull();

    await act(async () => {
      pending.resolve(status({ branch: "do-w2" }));
    });
    expect(await screen.findByText("do-w2")).toBeInTheDocument();
  });

  it("resposta atrasada do workspace antigo não aparece no novo", async () => {
    const old = deferred();
    fetchGitStatus.mockReturnValueOnce(old.promise);
    render(<GitStatusBadge />);
    await waitFor(() => expect(fetchGitStatus).toHaveBeenCalledWith("w1"));

    fetchGitStatus.mockResolvedValue(status({ branch: "do-w2" }));
    act(() => {
      useWorkspacesStore.setState({
        workspaces: [ws({ id: "w2" })],
        active_id: "w2",
      });
    });
    expect(await screen.findByText("do-w2")).toBeInTheDocument();

    await act(async () => {
      old.resolve(status({ branch: "do-w1" }));
    });

    expect(screen.queryByText("do-w1")).toBeNull();
    expect(screen.getByText("do-w2")).toBeInTheDocument();
  });
});
