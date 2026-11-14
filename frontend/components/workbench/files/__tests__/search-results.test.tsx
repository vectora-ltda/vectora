// @vitest-environment jsdom
/**
 * SearchResultsList — resultado da busca no conteúdo, agrupado por arquivo.
 * (O formato é o do backend: `{hits: [{path, line_number, line_text}], truncated}`.)
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_t, prop) => () => String(prop) }),
}));

import { groupHitsByFile, SearchResultsList } from "../search-results";
import type { SearchHit } from "../files-api";

afterEach(cleanup);

const hit = (path: string, line: number, text = "x"): SearchHit => ({
  path,
  line_number: line,
  line_text: text,
});

describe("groupHitsByFile", () => {
  it("agrupa por arquivo mantendo a ordem de chegada", () => {
    const grupos = groupHitsByFile([
      hit("b.ts", 1),
      hit("a.ts", 7),
      hit("b.ts", 9),
    ]);

    expect(grupos.map(([path, hits]) => [path, hits.length])).toEqual([
      ["b.ts", 2],
      ["a.ts", 1],
    ]);
  });

  it("sem hits não há grupos", () => {
    expect(groupHitsByFile([])).toEqual([]);
  });
});

describe("SearchResultsList", () => {
  it("busca ainda não feita não mostra nada", () => {
    const { container } = render(
      <SearchResultsList result={null} onOpenHit={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("sem resultados mostra o aviso", () => {
    render(
      <SearchResultsList
        result={{ hits: [], truncated: false }}
        onOpenHit={vi.fn()}
      />,
    );

    expect(
      screen.getByText("workbench_files_search_no_results"),
    ).toBeInTheDocument();
  });

  it("mostra um grupo por arquivo com a contagem e as linhas", () => {
    render(
      <SearchResultsList
        result={{
          hits: [
            hit("src/a.ts", 3, "const foo = 1"),
            hit("src/a.ts", 8, "foo()"),
            hit("src/b.ts", 20, "// foo"),
          ],
          truncated: false,
        }}
        onOpenHit={vi.fn()}
      />,
    );

    expect(screen.getByText("a.ts")).toBeInTheDocument();
    expect(screen.getByText("b.ts")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument(); // hits de a.ts
    expect(screen.getByText("const foo = 1")).toBeInTheDocument();
  });

  it("clicar numa linha abre o arquivo naquela linha", () => {
    const onOpenHit = vi.fn();
    render(
      <SearchResultsList
        result={{ hits: [hit("src/a.ts", 42, "alvo")], truncated: false }}
        onOpenHit={onOpenHit}
      />,
    );

    fireEvent.click(screen.getByText("alvo"));

    expect(onOpenHit).toHaveBeenCalledWith("src/a.ts", 42);
  });

  it("resultado truncado avisa", () => {
    render(
      <SearchResultsList
        result={{ hits: [hit("a.ts", 1)], truncated: true }}
        onOpenHit={vi.fn()}
      />,
    );

    expect(
      screen.getByText("workbench_files_search_truncated"),
    ).toBeInTheDocument();
  });

  it("resultado completo não avisa de truncamento", () => {
    render(
      <SearchResultsList
        result={{ hits: [hit("a.ts", 1)], truncated: false }}
        onOpenHit={vi.fn()}
      />,
    );

    expect(screen.queryByText("workbench_files_search_truncated")).toBeNull();
  });
});
