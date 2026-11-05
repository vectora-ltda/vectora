// @vitest-environment jsdom
/**
 * FileHistoryPanel — lista de commits que tocaram um arquivo (git log/file).
 * Estados: loading, vazio/null (erro de fetch tratado a montante como []),
 * lista com seleção.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

import { FileHistoryPanel } from "../file-history-panel";
import type { FileLogEntry } from "../files-api";

afterEach(() => {
  cleanup();
});

const ENTRIES: FileLogEntry[] = [
  {
    sha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    sha_short: "aaaaaaa",
    author: "Bruno Soares <bruno@example.com>",
    date: "2026-01-05T10:00:00Z",
    message: "fix: corrige bug de streaming",
  },
  {
    sha: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    sha_short: "bbbbbbb",
    author: "Outra Pessoa <outra@example.com>",
    date: "2026-01-04T10:00:00Z",
    message: "feat: adiciona busca",
  },
];

describe("FileHistoryPanel", () => {
  it("loading=true: renderiza o spinner e nenhum commit", () => {
    render(
      <FileHistoryPanel
        entries={null}
        loading={true}
        selectedSha={null}
        onSelectSha={vi.fn()}
      />,
    );
    expect(document.querySelector(".animate-spin")).not.toBeNull();
    expect(screen.queryByText(/corrige bug/)).toBeNull();
  });

  it("entries=null e loading=false: mostra mensagem de nenhum commit encontrado", () => {
    render(
      <FileHistoryPanel
        entries={null}
        loading={false}
        selectedSha={null}
        onSelectSha={vi.fn()}
      />,
    );
    expect(
      screen.getByText("Nenhum commit encontrado para este arquivo."),
    ).toBeInTheDocument();
  });

  it("entries=[] (array vazio, resposta de erro tratada a montante): mesma mensagem de vazio", () => {
    render(
      <FileHistoryPanel
        entries={[]}
        loading={false}
        selectedSha={null}
        onSelectSha={vi.fn()}
      />,
    );
    expect(
      screen.getByText("Nenhum commit encontrado para este arquivo."),
    ).toBeInTheDocument();
  });

  it("renderiza sha curto, data formatada, mensagem e autor sem o e-mail", () => {
    render(
      <FileHistoryPanel
        entries={ENTRIES}
        loading={false}
        selectedSha={null}
        onSelectSha={vi.fn()}
      />,
    );
    expect(screen.getByText("aaaaaaa")).toBeInTheDocument();
    expect(screen.getByText("05/01/2026")).toBeInTheDocument();
    expect(
      screen.getByText("fix: corrige bug de streaming"),
    ).toBeInTheDocument();
    expect(screen.getByText("Bruno Soares")).toBeInTheDocument();
  });

  it("clicar num commit chama onSelectSha com o sha completo", () => {
    const onSelectSha = vi.fn();
    render(
      <FileHistoryPanel
        entries={ENTRIES}
        loading={false}
        selectedSha={null}
        onSelectSha={onSelectSha}
      />,
    );
    fireEvent.click(screen.getByText("fix: corrige bug de streaming"));
    expect(onSelectSha).toHaveBeenCalledWith(
      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    );
  });

  it("selectedSha correspondente marca o commit selecionado com destaque visual", () => {
    render(
      <FileHistoryPanel
        entries={ENTRIES}
        loading={false}
        selectedSha={ENTRIES[0].sha}
        onSelectSha={vi.fn()}
      />,
    );
    const button = screen
      .getByText("fix: corrige bug de streaming")
      .closest("button") as HTMLButtonElement;
    expect(button.className).toContain("bg-primary/10");
  });
});
