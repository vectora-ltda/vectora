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
import { useEffect } from "react";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

vi.mock("../library-mcp-section", () => ({
  McpSection: ({
    onCountChange,
  }: {
    query: string;
    onCountChange: (count: number) => void;
  }) => {
    useEffect(() => {
      onCountChange(0);
    }, [onCountChange]);
    return <p>No MCP servers available yet.</p>;
  },
}));

vi.mock("../library-skills-section", () => ({
  SkillsSection: ({
    onCountChange,
  }: {
    query: string;
    onCountChange: (count: number) => void;
  }) => {
    useEffect(() => {
      onCountChange(0);
    }, [onCountChange]);
    return <p>No skills available yet.</p>;
  },
}));

vi.mock("../library-memory-section", () => ({
  MemorySection: ({
    onCountChange,
  }: {
    query: string;
    onCountChange: (count: number) => void;
  }) => {
    useEffect(() => {
      onCountChange(0);
    }, [onCountChange]);
    return <p>No memory buckets available yet.</p>;
  },
}));

import { LibraryTab } from "../library-tab";

afterEach(cleanup);

describe("LibraryTab", () => {
  it("renderiza as 3 seções (MCP, Skills, Memory Library)", () => {
    render(<LibraryTab threadId="t1" />);
    expect(screen.getByText(/MCP \(0\)/)).toBeTruthy();
    expect(screen.getByText(/Skills \(0\)/)).toBeTruthy();
    expect(screen.getByText(/Memory Library \(0\)/)).toBeTruthy();
  });

  it("mantém as três seções fechadas ao iniciar", () => {
    render(<LibraryTab threadId="t1" />);
    expect(screen.queryByText("No MCP servers available yet.")).toBeNull();
    expect(screen.queryByText("No skills available yet.")).toBeNull();
    expect(screen.queryByText("No memory buckets available yet.")).toBeNull();
  });

  it("cada seção vazia mostra estado vazio específico, sem quebrar", () => {
    render(<LibraryTab threadId="t1" />);
    fireEvent.click(screen.getByText(/MCP \(0\)/));
    fireEvent.click(screen.getByText(/Skills \(0\)/));
    fireEvent.click(screen.getByText(/Memory Library \(0\)/));
    expect(screen.getByText("No MCP servers available yet.")).toBeTruthy();
    expect(screen.getByText("No skills available yet.")).toBeTruthy();
    expect(screen.getByText("No memory buckets available yet.")).toBeTruthy();
  });

  it("abrir duas seções ao mesmo tempo mantém ambas abertas (type=multiple)", () => {
    render(<LibraryTab threadId="t1" />);
    fireEvent.click(screen.getByText(/MCP \(0\)/));
    fireEvent.click(screen.getByText(/Skills \(0\)/));
    fireEvent.click(screen.getByText(/Memory Library \(0\)/));
    expect(screen.getByText("No skills available yet.")).toBeTruthy();
    expect(screen.getByText("No memory buckets available yet.")).toBeTruthy();
  });

  it("desligar um filtro remove a seção correspondente sem afetar as outras", () => {
    render(<LibraryTab threadId="t1" />);
    fireEvent.click(screen.getByRole("button", { name: "Skills" }));
    expect(screen.queryByText(/Skills \(0\)/)).toBeNull();
    expect(screen.getByText(/MCP \(0\)/)).toBeTruthy();
    expect(screen.getByText(/Memory Library \(0\)/)).toBeTruthy();
  });

  it("todos os filtros desligados mostra estado vazio específico de 'nenhum filtro ativo'", () => {
    render(<LibraryTab threadId="t1" />);
    fireEvent.click(screen.getByRole("button", { name: "MCP" }));
    fireEvent.click(screen.getByRole("button", { name: "Skills" }));
    fireEvent.click(screen.getByRole("button", { name: "Memory" }));
    expect(
      screen.getByText(
        "No filters active — turn on at least one category to search.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/MCP \(0\)/)).toBeNull();
  });

  it("busca filtra o campo de texto sem quebrar com resultado vazio", () => {
    render(<LibraryTab threadId="t1" />);
    const input = screen.getByPlaceholderText("Search the Library…");
    fireEvent.change(input, { target: { value: "algo que não existe" } });
    // seções continuam montadas (vazias já eram, filtro não quebra nada)
    expect(screen.getByText(/MCP \(0\)/)).toBeTruthy();
  });
});
