// @vitest-environment jsdom
/**
 * Tests da paginação do HistoryView (offset 50 em 50) — regressão do pedido
 * de adicionar "carregar mais" ao histórico de commits, que antes só trazia
 * os primeiros 50 sem forma de avançar.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { CommitDetails, HistoryView } from "../history-view";
import * as api from "../api";
import { m } from "@/lib/paraglide/messages";

function commit(sha: string, body?: string) {
  return {
    sha,
    sha_short: sha.slice(0, 7),
    author: "Test <test@example.com>",
    date: "2026-01-01T00:00:00+00:00",
    message: `commit ${sha}`,
    ...(body === undefined ? {} : { body }),
    refs: [],
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CommitDetails — altura do cabeçalho", () => {
  it("abre no tamanho da descrição e permite reduzir o cabeçalho", () => {
    render(
      <CommitDetails
        commit={commit("with-body", "linha 1\nlinha 2\nlinha 3")}
        diff={null}
        loading={false}
      />,
    );

    const header = screen.getByTestId("commit-details-header");
    const separator = document.querySelector(
      '[role="separator"][aria-orientation="horizontal"]',
    );
    expect(separator).not.toBeNull();
    const initialHeight = Number.parseInt(header.style.height, 10);

    expect(initialHeight).toBeGreaterThan(96);
    expect(separator).toHaveAttribute("aria-valuenow", String(initialHeight));

    fireEvent.keyDown(separator!, { key: "ArrowUp" });

    expect(Number.parseInt(header.style.height, 10)).toBeLessThan(
      initialHeight,
    );
  });

  it("não cria resize nem altura fixa quando o commit não tem descrição", () => {
    render(
      <CommitDetails
        commit={commit("without-body")}
        diff={null}
        loading={false}
      />,
    );

    expect(screen.getByTestId("commit-details-header")).not.toHaveAttribute(
      "style",
    );
    expect(
      document.querySelector(
        '[role="separator"][aria-orientation="horizontal"]',
      ),
    ).not.toBeInTheDocument();
  });
});

describe("HistoryView — paginação", () => {
  it("carrega a primeira página com offset 0 e mostra o botão quando has_more", async () => {
    const spy = vi.spyOn(api, "fetchGitLog").mockResolvedValue({
      branch: "main",
      commits: [commit("a1"), commit("a2")],
      has_more: true,
    });

    render(<HistoryView workspaceId="ws1" onChanged={() => {}} />);

    await waitFor(() => expect(spy).toHaveBeenCalledWith("ws1", 0));
    expect(await screen.findByText("commit a1")).toBeInTheDocument();
    expect(screen.getByText("Load 50 more")).toBeInTheDocument();
  });

  it("clicar em 'carregar mais' busca com offset = nº de commits já carregados e concatena", async () => {
    const spy = vi
      .spyOn(api, "fetchGitLog")
      .mockResolvedValueOnce({
        branch: "main",
        commits: [commit("a1"), commit("a2")],
        has_more: true,
      })
      .mockResolvedValueOnce({
        branch: "main",
        commits: [commit("a3")],
        has_more: false,
      });

    render(<HistoryView workspaceId="ws1" onChanged={() => {}} />);
    await screen.findByText("commit a1");

    fireEvent.click(screen.getByText("Load 50 more"));

    await waitFor(() => expect(spy).toHaveBeenCalledWith("ws1", 2));
    expect(await screen.findByText("commit a3")).toBeInTheDocument();
    // Todos os commits anteriores continuam na lista (concatenado, não substituído).
    expect(screen.getByText("commit a1")).toBeInTheDocument();
    expect(screen.getByText("commit a2")).toBeInTheDocument();
    // has_more=false na 2ª página — botão some.
    expect(screen.queryByText("Load 50 more")).not.toBeInTheDocument();
  });

  it("sem has_more, não mostra o botão de carregar mais", async () => {
    vi.spyOn(api, "fetchGitLog").mockResolvedValue({
      branch: "main",
      commits: [commit("a1")],
      has_more: false,
    });

    render(<HistoryView workspaceId="ws1" onChanged={() => {}} />);

    await screen.findByText("commit a1");
    expect(screen.queryByText("Load 50 more")).not.toBeInTheDocument();
  });

  it("lista de commits tem min-h-0 (evita overflow quando o painel é redimensionado pequeno)", async () => {
    vi.spyOn(api, "fetchGitLog").mockResolvedValue({
      branch: "main",
      commits: [commit("a1")],
      has_more: false,
    });

    const { container } = render(
      <HistoryView workspaceId="ws1" onChanged={() => {}} />,
    );
    await screen.findByText("commit a1");

    const scrollable = container.querySelector(".overflow-y-auto");
    expect(scrollable).not.toBeNull();
    expect(scrollable?.className).toContain("min-h-0");
  });

  it("publica erro terminal quando o diff do commit falha", async () => {
    const commitDiff = vi
      .spyOn(api, "fetchCommitDiff")
      .mockRejectedValue(new Error("diff indisponível"));
    vi.spyOn(api, "fetchGitLog").mockResolvedValue({
      branch: "main",
      commits: [commit("failed-diff")],
      has_more: false,
    });
    const onOpenCommitDetails = vi.fn();

    render(
      <HistoryView
        workspaceId="ws1"
        onChanged={() => {}}
        onOpenCommitDetails={onOpenCommitDetails}
      />,
    );

    const commitButton = await screen.findByRole("button", {
      name: /commit failed-diff/,
    });
    fireEvent.click(commitButton);

    await waitFor(() => {
      expect(onOpenCommitDetails).toHaveBeenLastCalledWith({
        commit: commit("failed-diff"),
        diff: null,
        loading: false,
        error: m.workbench_git_operation_failed(),
      });
    });
    expect(commitDiff).toHaveBeenCalledWith("ws1", "failed-diff");
    expect(onOpenCommitDetails.mock.calls).toContainEqual([
      expect.objectContaining({ commit: commit("failed-diff") }),
    ]);
    expect(
      onOpenCommitDetails.mock.calls.some(
        ([details]) => details.loading === false,
      ),
    ).toBe(true);
  });
});

describe("HistoryView — menu de contexto (amend/squash/reorder/cherry-pick)", () => {
  it("cherry-pick chama apiCherryPick com o SHA do commit clicado", async () => {
    vi.spyOn(api, "fetchGitLog").mockResolvedValue({
      branch: "main",
      commits: [commit("a1"), commit("a2")],
      has_more: false,
    });
    const spy = vi
      .spyOn(api, "apiCherryPick")
      .mockResolvedValue({ status: "ok", message: "" });
    const onChanged = vi.fn();

    render(<HistoryView workspaceId="ws1" onChanged={onChanged} />);
    await screen.findByText("commit a1");

    fireEvent.contextMenu(screen.getByText("commit a1"));
    fireEvent.click(screen.getByText("Cherry-pick"));

    await waitFor(() => expect(spy).toHaveBeenCalledWith("ws1", "a1"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("mover pra baixo chama apiReorder com [commit, mais antigo]", async () => {
    vi.spyOn(api, "fetchGitLog").mockResolvedValue({
      branch: "main",
      commits: [commit("a1"), commit("a2")],
      has_more: false,
    });
    const spy = vi
      .spyOn(api, "apiReorder")
      .mockResolvedValue({ status: "ok", message: "" });

    render(<HistoryView workspaceId="ws1" onChanged={() => {}} />);
    await screen.findByText("commit a1");

    fireEvent.contextMenu(screen.getByText("commit a1"));
    fireEvent.click(screen.getByText("Move down"));

    await waitFor(() => expect(spy).toHaveBeenCalledWith("ws1", ["a1", "a2"]));
  });

  it("mover pra cima chama apiReorder com [mais recente, commit]", async () => {
    vi.spyOn(api, "fetchGitLog").mockResolvedValue({
      branch: "main",
      commits: [commit("a1"), commit("a2")],
      has_more: false,
    });
    const spy = vi
      .spyOn(api, "apiReorder")
      .mockResolvedValue({ status: "ok", message: "" });

    render(<HistoryView workspaceId="ws1" onChanged={() => {}} />);
    await screen.findByText("commit a2");

    fireEvent.contextMenu(screen.getByText("commit a2"));
    fireEvent.click(screen.getByText("Move up"));

    await waitFor(() => expect(spy).toHaveBeenCalledWith("ws1", ["a1", "a2"]));
  });

  it("commit mais recente não mostra 'Move up' (não há commit mais novo)", async () => {
    vi.spyOn(api, "fetchGitLog").mockResolvedValue({
      branch: "main",
      commits: [commit("a1"), commit("a2")],
      has_more: false,
    });

    render(<HistoryView workspaceId="ws1" onChanged={() => {}} />);
    await screen.findByText("commit a1");

    fireEvent.contextMenu(screen.getByText("commit a1"));
    expect(screen.queryByText("Move up")).not.toBeInTheDocument();
  });

  it("selecionar p/ squash + squashar até aqui chama apiSquash com base_ref e mensagem", async () => {
    vi.spyOn(api, "fetchGitLog").mockResolvedValue({
      branch: "main",
      commits: [commit("a1"), commit("a2"), commit("a3")],
      has_more: false,
    });
    const spy = vi
      .spyOn(api, "apiSquash")
      .mockResolvedValue({ status: "ok", message: "" });
    vi.spyOn(window, "prompt").mockReturnValue("feat: squash message");

    render(<HistoryView workspaceId="ws1" onChanged={() => {}} />);
    await screen.findByText("commit a3");

    // Seleciona a3 (mais antigo dos 3) como base do squash.
    fireEvent.contextMenu(screen.getByText("commit a3"));
    fireEvent.click(screen.getByText("Select for squash"));

    // No commit mais recente (a1), "squash up to here" cobre os 3 commits.
    fireEvent.contextMenu(screen.getByText("commit a1"));
    fireEvent.click(screen.getByText("Squash up to here (3 commits)"));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("ws1", "a3", "feat: squash message"),
    );
  });

  it("squash cancelado (prompt vazio) não chama apiSquash", async () => {
    vi.spyOn(api, "fetchGitLog").mockResolvedValue({
      branch: "main",
      commits: [commit("a1"), commit("a2")],
      has_more: false,
    });
    const spy = vi.spyOn(api, "apiSquash");
    vi.spyOn(window, "prompt").mockReturnValue(null);

    render(<HistoryView workspaceId="ws1" onChanged={() => {}} />);
    await screen.findByText("commit a2");

    fireEvent.contextMenu(screen.getByText("commit a2"));
    fireEvent.click(screen.getByText("Select for squash"));
    fireEvent.contextMenu(screen.getByText("commit a1"));
    fireEvent.click(screen.getByText("Squash up to here (2 commits)"));

    expect(spy).not.toHaveBeenCalled();
  });
});
