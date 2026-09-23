// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const state = {
  workspaces: [
    {
      id: "ws-1",
      name: "Vectora",
      cwd: "C:/Vectora",
      trusted: true,
      is_git_repo: true,
      git_remote: null,
      git_current_branch: null,
      git_default_branch: null,
    },
  ],
  active_id: "ws-1",
  safeRoots: [
    { id: "root-1", path: "C:/Projetos", label: "Projetos", builtin: false },
  ],
  status: "idle",
  error: null,
  hydrate: vi.fn(),
  loadSafeRoots: vi.fn(),
  setActive: vi.fn(),
};

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (selector: (value: typeof state) => unknown) =>
    selector(state),
}));

vi.mock("@/lib/hooks/use-network-status", () => ({
  useNetworkStatus: () => ({ offline: false }),
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get: (_target, property) => () => String(property),
    },
  ),
}));

vi.mock("../workspace-trust-dialog", () => ({
  WorkspaceTrustDialog: ({
    open,
    initialPath,
  }: {
    open: boolean;
    initialPath?: string;
  }) => (open ? <div data-testid="trust-dialog">{initialPath}</div> : null),
}));

import { WorkspaceSelector } from "../workspace-selector";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("WorkspaceSelector", () => {
  it("exibe workspaces e safe-roots já carregados pela raiz", () => {
    render(<WorkspaceSelector />);
    fireEvent.click(screen.getAllByRole("button")[0]);

    expect(screen.getAllByText("Vectora").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Projetos")).toBeInTheDocument();
  });

  it("abre o diálogo já posicionado na safe-root escolhida", () => {
    render(<WorkspaceSelector />);
    fireEvent.click(screen.getAllByRole("button")[0]);
    fireEvent.click(screen.getByText("Projetos"));

    expect(screen.getByTestId("trust-dialog")).toHaveTextContent("C:/Projetos");
  });
});
