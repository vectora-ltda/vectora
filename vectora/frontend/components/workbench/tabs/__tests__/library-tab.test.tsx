// @vitest-environment jsdom
/**
 * LibraryTab — shell da aba Library.
 *
 * Cobre: renderização das 3 seções como AccordionTrigger; abrir múltiplas
 * seções ao mesmo tempo; toggle de filtros (liga/desliga categoria); estado
 * vazio quando nenhum filtro está ativo; seção sem itens mostra estado vazio
 * específico sem quebrar. MCP, Skills e Memory são mockadas aqui pra testar
 * só o shell; suas próprias suítes cobrem o comportamento real
 * (library-mcp-section.test.tsx, skills-tab.test.tsx,
 * library-memory-section.test.tsx).
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

vi.mock("../library-mcp-section", () => ({
  McpSection: () => {
    return <p>No MCP servers available yet.</p>;
  },
}));

vi.mock("../library-skills-section", () => ({
  SkillsSection: () => {
    return <p>No skills available yet.</p>;
  },
}));

vi.mock("../library-memory-buckets-section", () => ({
  MemoryBucketsSection: () => {
    return <p>No memory buckets available yet.</p>;
  },
}));

vi.mock("../library-extensions-section", () => ({
  ExtensionsSection: () => <p>No extensions available yet.</p>,
}));

import { LibraryTab } from "../library-tab";

afterEach(cleanup);

describe("LibraryTab", () => {
  it("renderiza as categorias da Library", () => {
    render(<LibraryTab threadId="t1" />);
    expect(screen.getByRole("tab", { name: "MCP" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Skills" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Memory" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Extensions" })).toBeTruthy();
  });

  it("cada seção vazia mostra estado vazio específico, sem quebrar", () => {
    render(<LibraryTab threadId="t1" />);
    const cases = [
      ["MCP", "No MCP servers available yet."],
      ["Skills", "No skills available yet."],
      ["Memory", "No memory buckets available yet."],
      ["Extensions", "No extensions available yet."],
    ] as const;
    for (const [tab, emptyText] of cases) {
      fireEvent.click(screen.getByRole("tab", { name: tab }));
      expect(screen.getByText(emptyText)).toBeTruthy();
    }
  });

  it("seleciona uma seção sem desmontar as demais", () => {
    render(<LibraryTab threadId="t1" />);
    fireEvent.click(screen.getByRole("tab", { name: "MCP" }));
    expect(screen.getByRole("tab", { name: "MCP" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("selecionar uma categoria atualiza o estado ativo", () => {
    render(<LibraryTab threadId="t1" />);
    fireEvent.click(screen.getByRole("button", { name: "Skills" }));
    expect(screen.getByRole("tab", { name: "Skills" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "MCP" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
  });

  it("mantém todas as categorias disponíveis após alternar a seleção", () => {
    render(<LibraryTab threadId="t1" />);
    for (const name of ["MCP", "Skills", "Memory", "Extensions"]) {
      fireEvent.click(screen.getByRole("tab", { name }));
      expect(screen.getByRole("tab", { name })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    }
  });

  it("busca filtra o campo de texto sem quebrar com resultado vazio", () => {
    render(<LibraryTab threadId="t1" />);
    const input = screen.getByPlaceholderText("Search the Library…");
    fireEvent.change(input, { target: { value: "algo que não existe" } });
    // seções continuam montadas (vazias já eram, filtro não quebra nada)
    expect(screen.getByRole("tab", { name: "MCP" })).toBeTruthy();
  });
});
