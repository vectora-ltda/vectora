// @vitest-environment jsdom
/**
 * LibraryTab — shell da aba Library.
 *
 * Cobre: renderização das 3 seções como AccordionTrigger; seleção única dos
 * filtros; exclusão mútua das seções abertas; e o viewport de scroll interno.
 * MCP, Skills e Memory são mockadas aqui pra testar só o shell; suas próprias
 * suítes cobrem o comportamento real (library-mcp-section.test.tsx,
 * skills-tab.test.tsx, library-memory-section.test.tsx).
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

  it("abre uma seção por vez e fecha a anterior", () => {
    render(<LibraryTab threadId="t1" />);
    fireEvent.click(screen.getByText(/MCP \(0\)/));
    expect(screen.getByText("No MCP servers available yet.")).toBeTruthy();

    fireEvent.click(screen.getByText(/Skills \(0\)/));
    expect(screen.getByText("No skills available yet.")).toBeTruthy();
    expect(screen.queryByText("No MCP servers available yet.")).toBeNull();
    expect(screen.queryByText("No memory buckets available yet.")).toBeNull();
  });

  it("mantém as três categorias no seletor e ativa apenas uma tab", () => {
    render(<LibraryTab threadId="t1" />);
    const mcpTab = screen.getByRole("tab", { name: "MCP" });
    const skillsTab = screen.getByRole("tab", { name: "Skills" });
    const memoryTab = screen.getByRole("tab", { name: "Memory" });

    expect(mcpTab).toHaveAttribute("aria-selected", "false");
    expect(skillsTab).toHaveAttribute("aria-selected", "false");
    expect(memoryTab).toHaveAttribute("aria-selected", "false");

    fireEvent.click(skillsTab);
    expect(skillsTab).toHaveAttribute("aria-selected", "true");
    expect(mcpTab).toHaveAttribute("aria-selected", "false");
    expect(memoryTab).toHaveAttribute("aria-selected", "false");

    fireEvent.click(mcpTab);
    expect(mcpTab).toHaveAttribute("aria-selected", "true");
    expect(skillsTab).toHaveAttribute("aria-selected", "false");
    expect(memoryTab).toHaveAttribute("aria-selected", "false");
  });

  it("clicar em uma tab abre sua seção correspondente", () => {
    render(<LibraryTab threadId="t1" />);
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    expect(screen.getByText("No memory buckets available yet.")).toBeTruthy();
    expect(screen.queryByText("No MCP servers available yet.")).toBeNull();
  });

  it("permite recolher a seção ativa sem ativar outra", () => {
    render(<LibraryTab threadId="t1" />);
    const memoryHeader = screen.getByText(/Memory Library \(0\)/);
    fireEvent.click(memoryHeader);
    expect(screen.getByText("No memory buckets available yet.")).toBeTruthy();
    fireEvent.click(memoryHeader);
    expect(screen.queryByText("No memory buckets available yet.")).toBeNull();
    expect(screen.getByRole("tab", { name: "Memory" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
  });

  it("aplica o scroll ao conteúdo interno da seção aberta", () => {
    render(<LibraryTab threadId="t1" />);
    fireEvent.click(screen.getByRole("tab", { name: "MCP" }));
    const content = screen
      .getByText("No MCP servers available yet.")
      .closest('[data-slot="accordion-content"]');
    expect(content).toHaveClass("overflow-hidden");
    expect(content?.firstElementChild).toHaveClass("overflow-y-auto");
  });

  it("busca filtra o campo de texto sem quebrar com resultado vazio", () => {
    render(<LibraryTab threadId="t1" />);
    const input = screen.getByPlaceholderText("Search the Library…");
    fireEvent.change(input, { target: { value: "algo que não existe" } });
    // seções continuam montadas (vazias já eram, filtro não quebra nada)
    expect(screen.getByText(/MCP \(0\)/)).toBeTruthy();
  });
});
