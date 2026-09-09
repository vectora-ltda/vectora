// @vitest-environment jsdom
/**
 * Testes do ChangesView — grupos Staged/Modificados/Untracked, ações inline
 * de arquivo (stage/unstage/discard) e painel de commit.
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { ChangesView } from "../changes-view";
import * as api from "../api";
import type { DiffFile, DiffSummary } from "@/lib/stores/workbench-store";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get: (_target, prop) => (args?: Record<string, unknown>) =>
        args ? `${String(prop)}(${JSON.stringify(args)})` : String(prop),
    },
  ),
}));

const mockDiffState = {
  openFiles: [] as string[],
  hunksByFile: {} as Record<string, unknown>,
  fileFetchedAt: {} as Record<string, number>,
};

const mockGitOps = {
  selectedFiles: [] as string[],
  selectedHunks: {},
  activeDocument: null,
  operation: null,
};

const mockWorkbench = {
  getDiff: (_id: string) => mockDiffState,
  getGitOps: (_id: string) => mockGitOps,
  setDiffOpenFile: vi.fn(),
  setDiffHunks: vi.fn(),
  invalidateDiff: vi.fn(),
  clearGitSelection: vi.fn(),
  toggleGitFileSelection: vi.fn(),
};

vi.mock("@/lib/stores/workbench-store", () => ({
  WORKBENCH_STALE_MS: 30000,
  useWorkbenchStore: (sel: (s: typeof mockWorkbench) => unknown) =>
    sel(mockWorkbench),
}));

vi.mock("@/lib/hooks/workbench/use-swr", () => ({
  useWorkbenchSWR: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  mockDiffState.openFiles = [];
  mockDiffState.hunksByFile = {};
  mockDiffState.fileFetchedAt = {};
  mockGitOps.selectedFiles = [];
  vi.clearAllMocks();
});

function file(overrides: Partial<DiffFile>): DiffFile {
  return {
    path: "src/a.ts",
    status: "M",
    additions: 1,
    deletions: 0,
    staged_change: null,
    unstaged_change: null,
    untracked: false,
    ...overrides,
  };
}

function summary(files: DiffFile[]): DiffSummary {
  return {
    is_git_repo: true,
    total_additions: files.reduce((n, f) => n + f.additions, 0),
    total_deletions: files.reduce((n, f) => n + f.deletions, 0),
    files,
  };
}

describe("ChangesView", () => {
  it("filtra a seleção mista e mostra contagens elegíveis por ação", async () => {
    mockGitOps.selectedFiles = ["staged.ts", "modified.ts", "new.ts"];
    const spy = vi
      .spyOn(api, "apiGitFileAction")
      .mockResolvedValue({ status: "ok", message: "" });
    render(
      <ChangesView
        workspaceId="ws1"
        summary={summary([
          file({ path: "staged.ts", staged_change: "M" }),
          file({ path: "modified.ts", unstaged_change: "M" }),
          file({ path: "new.ts", untracked: true }),
        ])}
      />,
    );

    fireEvent.click(screen.getByText(/workbench_git_stage_selected/));
    await waitFor(() =>
      expect(spy.mock.calls).toEqual([
        ["ws1", "stage", "modified.ts"],
        ["ws1", "stage", "new.ts"],
      ]),
    );
    expect(
      screen.getByText(/workbench_git_unstage_selected/),
    ).toBeInTheDocument();
  });

  it("mantém somente caminhos que falharam e exibe erro em lote", async () => {
    mockGitOps.selectedFiles = ["ok.ts", "failed.ts"];
    const spy = vi
      .spyOn(api, "apiGitFileAction")
      .mockImplementation(async (_ws, _action, path) =>
        path === "failed.ts"
          ? { status: "error", message: "falhou" }
          : { status: "ok", message: "" },
      );
    render(
      <ChangesView
        workspaceId="ws1"
        summary={summary([
          file({ path: "ok.ts", unstaged_change: "M" }),
          file({ path: "failed.ts", unstaged_change: "M" }),
        ])}
      />,
    );

    fireEvent.click(screen.getByText(/workbench_git_stage_selected/));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(mockWorkbench.toggleGitFileSelection).toHaveBeenCalledWith(
      "ws1",
      "failed.ts",
    );
  });

  it("preserva seleção elegível para outra ação em uma seleção mista", async () => {
    mockGitOps.selectedFiles = ["staged.ts", "modified.ts"];
    vi.spyOn(api, "apiGitFileAction").mockResolvedValue({
      status: "ok",
      message: "",
    });
    render(
      <ChangesView
        workspaceId="ws1"
        summary={summary([
          file({ path: "staged.ts", staged_change: "M" }),
          file({ path: "modified.ts", unstaged_change: "M" }),
        ])}
      />,
    );

    fireEvent.click(screen.getByText(/workbench_git_stage_selected/));
    await waitFor(() =>
      expect(mockWorkbench.toggleGitFileSelection).toHaveBeenCalledWith(
        "ws1",
        "staged.ts",
      ),
    );
  });

  it("ignora a pasta pai de um arquivo aninhado e não oferece pasta para a raiz", () => {
    vi.spyOn(api, "apiGitignoreAppend").mockResolvedValue({
      status: "ok",
      message: "",
    });
    const nested = file({
      path: "src/components/Button.tsx",
      unstaged_change: "M",
    });
    const root = file({ path: "README.md", unstaged_change: "M" });
    render(<ChangesView workspaceId="ws1" summary={summary([nested, root])} />);

    fireEvent.contextMenu(screen.getByText("src/components/Button.tsx"));
    expect(
      screen.getByRole("menuitem", { name: "Ignore folder" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Ignore folder" }));
    expect(api.apiGitignoreAppend).toHaveBeenCalledWith(
      "ws1",
      "src/components",
      true,
    );

    fireEvent.contextMenu(screen.getByText("README.md"));
    expect(
      screen.queryByRole("menuitem", { name: "Ignore folder" }),
    ).not.toBeInTheDocument();
  });

  it("mostra estado vazio (working tree limpo) quando summary.files está vazio", () => {
    render(<ChangesView workspaceId="ws1" summary={summary([])} />);
    expect(screen.getByText("workbench_diff_clean")).toBeInTheDocument();
  });

  it("agrupa arquivos staged, modificados e untracked corretamente", () => {
    const files = [
      file({ path: "staged.ts", staged_change: "M" }),
      file({ path: "modified.ts", unstaged_change: "M" }),
      file({ path: "new.ts", untracked: true }),
    ];
    render(<ChangesView workspaceId="ws1" summary={summary(files)} />);

    expect(screen.getByText("staged.ts")).toBeInTheDocument();
    expect(screen.getByText("modified.ts")).toBeInTheDocument();
    expect(screen.getByText("new.ts")).toBeInTheDocument();
    expect(
      screen.getByText("workbench_diff_group_untracked"),
    ).toBeInTheDocument();
  });

  it("não mostra o grupo untracked quando não há arquivos untracked", () => {
    render(
      <ChangesView
        workspaceId="ws1"
        summary={summary([file({ path: "a.ts", staged_change: "M" })])}
      />,
    );
    expect(
      screen.queryByText("workbench_diff_group_untracked"),
    ).not.toBeInTheDocument();
  });

  it("clicar em stage (+) chama apiGitFileAction e invalida o diff", async () => {
    const spy = vi
      .spyOn(api, "apiGitFileAction")
      .mockResolvedValue({ status: "ok", message: "" });
    render(
      <ChangesView
        workspaceId="ws1"
        summary={summary([file({ path: "a.ts", unstaged_change: "M" })])}
      />,
    );

    fireEvent.click(screen.getByTitle("workbench_git_ctx_stage"));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("ws1", "stage", "a.ts"),
    );
    await waitFor(() =>
      expect(mockWorkbench.invalidateDiff).toHaveBeenCalledWith("ws1"),
    );
  });

  it("clicar em unstage (−) chama apiGitFileAction com unstage", async () => {
    const spy = vi
      .spyOn(api, "apiGitFileAction")
      .mockResolvedValue({ status: "ok", message: "" });
    render(
      <ChangesView
        workspaceId="ws1"
        summary={summary([file({ path: "a.ts", staged_change: "M" })])}
      />,
    );

    fireEvent.click(screen.getByTitle("workbench_git_ctx_unstage"));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("ws1", "unstage", "a.ts"),
    );
  });

  it("clicar em discard (↩) abre o dialog de confirmação e só chama a API ao confirmar", async () => {
    const spy = vi
      .spyOn(api, "apiGitFileAction")
      .mockResolvedValue({ status: "ok", message: "" });
    render(
      <ChangesView
        workspaceId="ws1"
        summary={summary([
          file({ path: "a.ts", unstaged_change: "M", untracked: false }),
        ])}
      />,
    );

    fireEvent.click(screen.getByTitle("workbench_git_ctx_discard"));
    expect(screen.getByText("workbench_git_discard_title")).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("workbench_git_discard_confirm"));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("ws1", "discard", "a.ts"),
    );
  });

  it("botão de commit fica desabilitado com mensagem vazia e habilita ao digitar", () => {
    render(
      <ChangesView
        workspaceId="ws1"
        summary={summary([file({ path: "a.ts", staged_change: "M" })])}
      />,
    );
    const btn = screen
      .getByText("workbench_diff_commit_button")
      .closest("button")!;
    expect(btn).toBeDisabled();

    fireEvent.change(
      screen.getByPlaceholderText("workbench_diff_commit_placeholder"),
      { target: { value: "fix: bug" } },
    );
    expect(btn).not.toBeDisabled();
  });

  it("clicar em commit chama apiGitCommit com a mensagem e limpa o campo em sucesso", async () => {
    const spy = vi
      .spyOn(api, "apiGitCommit")
      .mockResolvedValue({ status: "ok", message: "" });
    render(
      <ChangesView
        workspaceId="ws1"
        summary={summary([file({ path: "a.ts", staged_change: "M" })])}
      />,
    );

    const input = screen.getByPlaceholderText(
      "workbench_diff_commit_placeholder",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "fix: bug" } });
    fireEvent.click(screen.getByText("workbench_diff_commit_button"));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("ws1", "fix: bug", false, {
        body: "",
        amend: false,
      }),
    );
    await waitFor(() => expect(input.value).toBe(""));
  });

  it("commit com erro mantém a mensagem no campo (não limpa)", async () => {
    vi.spyOn(api, "apiGitCommit").mockResolvedValue({
      status: "error",
      message: "falhou",
    });
    render(
      <ChangesView
        workspaceId="ws1"
        summary={summary([file({ path: "a.ts", staged_change: "M" })])}
      />,
    );

    const input = screen.getByPlaceholderText(
      "workbench_diff_commit_placeholder",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "fix: bug" } });
    fireEvent.click(screen.getByText("workbench_diff_commit_button"));

    await waitFor(() => expect(api.apiGitCommit).toHaveBeenCalled());
    expect(input.value).toBe("fix: bug");
  });

  it("preenche descrição e marca amend: passa body e amend pro apiGitCommit", async () => {
    const spy = vi
      .spyOn(api, "apiGitCommit")
      .mockResolvedValue({ status: "ok", message: "" });
    render(
      <ChangesView
        workspaceId="ws1"
        summary={summary([file({ path: "a.ts", staged_change: "M" })])}
      />,
    );

    fireEvent.change(
      screen.getByPlaceholderText("workbench_diff_commit_placeholder"),
      { target: { value: "fix: bug" } },
    );
    fireEvent.change(
      screen.getByPlaceholderText("workbench_diff_commit_body_placeholder"),
      { target: { value: "detalhes" } },
    );
    fireEvent.click(screen.getByTestId("git-commit-amend"));
    fireEvent.click(screen.getByText("workbench_diff_commit_button"));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("ws1", "fix: bug", false, {
        body: "detalhes",
        amend: true,
      }),
    );
  });

  it("sem descrição: passa body vazio (regressão — commit simples continua funcionando)", async () => {
    const spy = vi
      .spyOn(api, "apiGitCommit")
      .mockResolvedValue({ status: "ok", message: "" });
    render(
      <ChangesView
        workspaceId="ws1"
        summary={summary([file({ path: "a.ts", staged_change: "M" })])}
      />,
    );

    fireEvent.change(
      screen.getByPlaceholderText("workbench_diff_commit_placeholder"),
      { target: { value: "fix: bug" } },
    );
    fireEvent.click(screen.getByText("workbench_diff_commit_button"));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("ws1", "fix: bug", false, {
        body: "",
        amend: false,
      }),
    );
  });

  it("clicar no nome do arquivo expande e chama setDiffOpenFile", () => {
    render(
      <ChangesView
        workspaceId="ws1"
        summary={summary([file({ path: "a.ts", staged_change: "M" })])}
      />,
    );
    fireEvent.click(screen.getByText("a.ts"));
    expect(mockWorkbench.setDiffOpenFile).toHaveBeenCalledWith(
      "ws1",
      "a.ts",
      true,
    );
  });
});
