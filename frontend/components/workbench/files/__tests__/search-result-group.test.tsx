// @vitest-environment jsdom
/**
 * SearchResultGroup — grupo colapsável de resultados de busca em conteúdo.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

import { SearchResultGroup } from "../search-result-group";
import type { SearchHit } from "../files-api";

afterEach(() => {
  cleanup();
});

const HITS: SearchHit[] = [
  { path: "src/a.ts", line_number: 3, line_text: "  const x = 1;" },
  { path: "src/a.ts", line_number: 10, line_text: "  return x;" },
];

describe("SearchResultGroup", () => {
  it("renderiza o basename do arquivo e a contagem de hits", () => {
    render(
      <SearchResultGroup filePath="src/a.ts" hits={HITS} onOpenHit={vi.fn()} />,
    );
    expect(screen.getByText("a.ts")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("renderiza cada hit com número de linha e texto sem indentação à esquerda", () => {
    render(
      <SearchResultGroup filePath="src/a.ts" hits={HITS} onOpenHit={vi.fn()} />,
    );
    expect(screen.getByText("const x = 1;")).toBeInTheDocument();
    expect(screen.getByText("return x;")).toBeInTheDocument();
  });

  it("clicar num hit chama onOpenHit com path e line_number corretos", () => {
    const onOpenHit = vi.fn();
    render(
      <SearchResultGroup
        filePath="src/a.ts"
        hits={HITS}
        onOpenHit={onOpenHit}
      />,
    );
    fireEvent.click(screen.getByText("const x = 1;"));
    expect(onOpenHit).toHaveBeenCalledWith("src/a.ts", 3);
  });

  it("clicar no cabeçalho colapsa o grupo, escondendo os hits", () => {
    render(
      <SearchResultGroup filePath="src/a.ts" hits={HITS} onOpenHit={vi.fn()} />,
    );
    expect(screen.getByText("const x = 1;")).toBeInTheDocument();
    fireEvent.click(screen.getByText("a.ts"));
    expect(screen.queryByText("const x = 1;")).toBeNull();
  });

  it("clicar novamente no cabeçalho expande de volta", () => {
    render(
      <SearchResultGroup filePath="src/a.ts" hits={HITS} onOpenHit={vi.fn()} />,
    );
    const header = screen.getByText("a.ts");
    fireEvent.click(header);
    fireEvent.click(header);
    expect(screen.getByText("const x = 1;")).toBeInTheDocument();
  });

  it("path sem barra usa o próprio path como nome de exibição", () => {
    render(
      <SearchResultGroup filePath="README.md" hits={[]} onOpenHit={vi.fn()} />,
    );
    expect(screen.getByText("README.md")).toBeInTheDocument();
  });

  it("lista de hits vazia: renderiza contagem 0 sem quebrar", () => {
    render(
      <SearchResultGroup
        filePath="src/empty.ts"
        hits={[]}
        onOpenHit={vi.fn()}
      />,
    );
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});
