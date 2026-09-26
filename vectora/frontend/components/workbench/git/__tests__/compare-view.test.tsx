// @vitest-environment jsdom
/**
 * Testes do CompareView — comparação entre refs, expandir diff por arquivo,
 * merge (sucesso/conflito/erro) e resolução de conflitos.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { CompareView } from "../compare-view";
import * as api from "../api";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get: (_target, prop) => (args?: Record<string, unknown>) =>
        args ? `${String(prop)}(${JSON.stringify(args)})` : String(prop),
    },
  ),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderCompare(
  overrides: Partial<Parameters<typeof CompareView>[0]> = {},
) {
  const onBack = vi.fn();
  const onChanged = vi.fn();
  const onOpenPR = vi.fn();
  const utils = render(
    <CompareView
      workspaceId="ws1"
      branches={["main", "feature"]}
      current="main"
      onBack={onBack}
      onChanged={onChanged}
      onOpenPR={onOpenPR}
      {...overrides}
    />,
  );
  return { ...utils, onBack, onChanged, onOpenPR };
}

describe("CompareView", () => {
  it("chama apiCompare automaticamente com base=current e head=primeira branch diferente", async () => {
    const spy = vi.spyOn(api, "apiCompare").mockResolvedValue({
      base: "main",
      head: "feature",
      ahead: 1,
      behind: 0,
      files: [],
      truncated: false,
    });
    renderCompare();
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("ws1", "main", "feature"),
    );
  });

  it("mostra placeholder de nenhum arquivo quando compare retorna files vazio", async () => {
    vi.spyOn(api, "apiCompare").mockResolvedValue({
      base: "main",
      head: "feature",
      ahead: 0,
      behind: 0,
      files: [],
      truncated: false,
    });
    renderCompare();
    expect(
      await screen.findByText("workbench_git_compare_no_files"),
    ).toBeInTheDocument();
  });

  it("mostra null (sem chamar apiCompare) quando base===head", async () => {
    const spy = vi.spyOn(api, "apiCompare");
    renderCompare({ branches: ["main"], current: "main" });
    await waitFor(() =>
      expect(
        screen.getByText("workbench_git_compare_no_files"),
      ).toBeInTheDocument(),
    );
    expect(spy).not.toHaveBeenCalled();
  });

  it("renderiza a lista de arquivos alterados com resumo ahead/behind", async () => {
    vi.spyOn(api, "apiCompare").mockResolvedValue({
      base: "main",
      head: "feature",
      ahead: 2,
      behind: 1,
      files: [{ path: "a.ts", status: "M", additions: 3, deletions: 1 }],
      truncated: false,
    });
    renderCompare();
    expect(await screen.findByText("a.ts")).toBeInTheDocument();
  });

  it("clicar num arquivo expande e busca os hunks via apiCompareFile (lazy, só uma vez)", async () => {
    vi.spyOn(api, "apiCompare").mockResolvedValue({
      base: "main",
      head: "feature",
      ahead: 1,
      behind: 0,
      files: [{ path: "a.ts", status: "M", additions: 1, deletions: 0 }],
      truncated: false,
    });
    const fileSpy = vi
      .spyOn(api, "apiCompareFile")
      .mockResolvedValue([{ header: "@@ -1 +1 @@", lines: ["+x"] }]);
    renderCompare();
    const row = await screen.findByText("a.ts");

    fireEvent.click(row);
    await waitFor(() =>
      expect(fileSpy).toHaveBeenCalledWith("ws1", "main", "feature", "a.ts"),
    );
    expect(await screen.findByText("x")).toBeInTheDocument();

    fireEvent.click(row);
    fireEvent.click(row);
    expect(fileSpy).toHaveBeenCalledTimes(1);
  });

  it("clicar em voltar chama onBack", async () => {
    vi.spyOn(api, "apiCompare").mockResolvedValue({
      base: "main",
      head: "feature",
      ahead: 0,
      behind: 0,
      files: [],
      truncated: false,
    });
    const { onBack } = renderCompare();
    await screen.findByText("workbench_git_compare_no_files");
    fireEvent.click(screen.getByTitle("workbench_git_back"));
    expect(onBack).toHaveBeenCalled();
  });

  it("merge com sucesso mostra mensagem de ok, chama onChanged e refaz o compare", async () => {
    const compareSpy = vi.spyOn(api, "apiCompare").mockResolvedValue({
      base: "main",
      head: "feature",
      ahead: 1,
      behind: 0,
      files: [],
      truncated: false,
    });
    vi.spyOn(api, "apiMerge").mockResolvedValue({
      status: "ok",
      message: "",
      conflicts: [],
    });
    const { onChanged } = renderCompare();
    await waitFor(() => expect(compareSpy).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByText(/workbench_git_merge_into/));
    expect(
      await screen.findByText("workbench_git_merge_ok"),
    ).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
    await waitFor(() => expect(compareSpy).toHaveBeenCalledTimes(2));
  });

  it("merge com conflito mostra os arquivos em conflito e permite resolver ours/theirs", async () => {
    vi.spyOn(api, "apiCompare").mockResolvedValue({
      base: "main",
      head: "feature",
      ahead: 1,
      behind: 0,
      files: [],
      truncated: false,
    });
    vi.spyOn(api, "apiMerge").mockResolvedValue({
      status: "conflict",
      message: "",
      conflicts: ["a.ts"],
    });
    const resolveSpy = vi
      .spyOn(api, "apiResolveConflict")
      .mockResolvedValue({ status: "ok", message: "" });
    renderCompare();

    fireEvent.click(await screen.findByText(/workbench_git_merge_into/));
    expect(
      await screen.findByText("workbench_diff_conflicts_ours"),
    ).toBeInTheDocument();
    expect(screen.getByText("a.ts")).toBeInTheDocument();

    fireEvent.click(screen.getByText("workbench_diff_conflicts_ours"));
    await waitFor(() =>
      expect(resolveSpy).toHaveBeenCalledWith("ws1", "a.ts", "ours"),
    );
    await waitFor(() =>
      expect(screen.queryByText("a.ts")).not.toBeInTheDocument(),
    );
  });

  it("merge com erro mostra a mensagem de erro retornada pela API", async () => {
    vi.spyOn(api, "apiCompare").mockResolvedValue({
      base: "main",
      head: "feature",
      ahead: 1,
      behind: 0,
      files: [],
      truncated: false,
    });
    vi.spyOn(api, "apiMerge").mockResolvedValue({
      status: "error",
      message: "algo deu errado",
      conflicts: [],
    });
    renderCompare();

    fireEvent.click(await screen.findByText(/workbench_git_merge_into/));
    expect(await screen.findByText("algo deu errado")).toBeInTheDocument();
  });

  it("botão de merge fica desabilitado quando head === current", async () => {
    vi.spyOn(api, "apiCompare");
    renderCompare({ branches: ["main"], current: "main" });
    const btn = (await screen.findByText(/workbench_git_merge_into/)).closest(
      "button",
    )!;
    expect(btn).toBeDisabled();
  });

  it("clicar em criar PR chama onOpenPR com o head selecionado", async () => {
    vi.spyOn(api, "apiCompare").mockResolvedValue({
      base: "main",
      head: "feature",
      ahead: 1,
      behind: 0,
      files: [],
      truncated: false,
    });
    const { onOpenPR } = renderCompare();
    await screen.findByText("workbench_git_compare_no_files");
    fireEvent.click(screen.getByText("workbench_git_pr_create"));
    expect(onOpenPR).toHaveBeenCalledWith("feature");
  });
});
