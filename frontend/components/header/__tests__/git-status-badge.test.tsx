// @vitest-environment jsdom
/**
 * Tests para GitStatusBadge: renderiza a branch do workspace git ativo e
 * fica oculto quando não há repo git.
 */

import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { GitStatusBadge } from "../git-status-badge";
import {
  useWorkspacesStore,
  type WorkspaceInfo,
} from "@/lib/stores/workspaces-store";

function ws(over: Partial<WorkspaceInfo>): WorkspaceInfo {
  return {
    id: "w1",
    name: "w1",
    cwd: "/x",
    is_git_repo: false,
    ...over,
  } as unknown as WorkspaceInfo;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  useWorkspacesStore.setState({ workspaces: [], active_id: null });
});

describe("GitStatusBadge", () => {
  it("não renderiza nada quando o workspace não é repo git", () => {
    useWorkspacesStore.setState({
      workspaces: [ws({ id: "w1", is_git_repo: false })],
      active_id: "w1",
    });
    const { container } = render(<GitStatusBadge />);
    expect(container).toBeEmptyDOMElement();
  });

  it("mostra a branch após buscar o status do workspace git", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ workspace: { git_current_branch: "feature-x" } }),
      })),
    );
    useWorkspacesStore.setState({
      workspaces: [ws({ id: "w1", is_git_repo: true })],
      active_id: "w1",
    });
    render(<GitStatusBadge />);
    await waitFor(() =>
      expect(screen.getByText("feature-x")).toBeInTheDocument(),
    );
  });
});
