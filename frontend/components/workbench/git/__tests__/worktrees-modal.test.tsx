// @vitest-environment jsdom
/**
 * Testes do WorktreesModal — lista/cria worktrees.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { WorktreesModal } from "../worktrees-modal";
import * as api from "../api";

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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WorktreesModal", () => {
  it("carrega a lista ao abrir e mostra estado vazio quando não há worktrees", async () => {
    const spy = vi.spyOn(api, "fetchWorktrees").mockResolvedValue([]);

    render(
      <WorktreesModal workspaceId="ws1" open={true} onOpenChange={() => {}} />,
    );

    await waitFor(() => expect(spy).toHaveBeenCalledWith("ws1"));
    expect(
      await screen.findByText("workbench_diff_worktree_empty"),
    ).toBeInTheDocument();
  });

  it("não carrega a lista quando o modal está fechado", () => {
    const spy = vi.spyOn(api, "fetchWorktrees");
    render(
      <WorktreesModal workspaceId="ws1" open={false} onOpenChange={() => {}} />,
    );
    expect(spy).not.toHaveBeenCalled();
  });

  it("renderiza cada worktree com path e branch, quando presente", async () => {
    vi.spyOn(api, "fetchWorktrees").mockResolvedValue([
      { path: "/repo/worktrees/feat", branch: "feature/x" },
      { path: "/repo/worktrees/nobrn" },
    ]);

    render(
      <WorktreesModal workspaceId="ws1" open={true} onOpenChange={() => {}} />,
    );

    expect(await screen.findByText("feat")).toBeInTheDocument();
    expect(screen.getByText("feature/x")).toBeInTheDocument();
    expect(screen.getByText("nobrn")).toBeInTheDocument();
  });

  it("criar worktree com nome vazio não chama a API", async () => {
    vi.spyOn(api, "fetchWorktrees").mockResolvedValue([]);
    const createSpy = vi.spyOn(api, "apiCreateWorktree");

    render(
      <WorktreesModal workspaceId="ws1" open={true} onOpenChange={() => {}} />,
    );
    await screen.findByText("workbench_diff_worktree_empty");

    fireEvent.click(screen.getByText("workbench_diff_worktree_create"));
    expect(createSpy).not.toHaveBeenCalled();
  });

  it("preencher nome e branch e criar chama a API e recarrega a lista", async () => {
    vi.spyOn(api, "fetchWorktrees")
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        { path: "/repo/worktrees/feat", branch: "main" },
      ]);
    const createSpy = vi
      .spyOn(api, "apiCreateWorktree")
      .mockResolvedValue(true);

    render(
      <WorktreesModal workspaceId="ws1" open={true} onOpenChange={() => {}} />,
    );
    await screen.findByText("workbench_diff_worktree_empty");

    fireEvent.change(
      screen.getByPlaceholderText("workbench_diff_worktree_name_placeholder"),
      { target: { value: "feat" } },
    );
    fireEvent.change(
      screen.getByPlaceholderText("workbench_diff_worktree_branch_placeholder"),
      { target: { value: "main" } },
    );
    fireEvent.click(screen.getByText("workbench_diff_worktree_create"));

    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith("ws1", "feat", "main"),
    );
    expect(await screen.findByText("feat")).toBeInTheDocument();
  });

  it("branch vazia é enviada como undefined (não string vazia)", async () => {
    vi.spyOn(api, "fetchWorktrees").mockResolvedValue([]);
    const createSpy = vi
      .spyOn(api, "apiCreateWorktree")
      .mockResolvedValue(true);

    render(
      <WorktreesModal workspaceId="ws1" open={true} onOpenChange={() => {}} />,
    );
    await screen.findByText("workbench_diff_worktree_empty");

    fireEvent.change(
      screen.getByPlaceholderText("workbench_diff_worktree_name_placeholder"),
      { target: { value: "feat" } },
    );
    fireEvent.click(screen.getByText("workbench_diff_worktree_create"));

    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith("ws1", "feat", undefined),
    );
  });
});
