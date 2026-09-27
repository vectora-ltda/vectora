// @vitest-environment jsdom
/**
 * LibraryTab — shell da aba Library.
 *
 * Cobre: renderização das 3 seções como AccordionTrigger; seleção única dos
 * filtros; exclusão mútua das seções abertas; e o viewport de scroll interno.
 * MCP, Skills e Memory são mockadas aqui pra testar só o shell; suas próprias
 * suítes cobrem o comportamento real (library-mcp-section.test.tsx,
 * skills-tab.test.tsx, library-memory-buckets-section.test.tsx).
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

vi.mock("../library-mcp-section", () => ({
  McpSection: () =>
    fixtures.populated ? (
      <article>Brave Search MCP</article>
    ) : (
      <p>No MCP servers available yet.</p>
    ),
}));

vi.mock("../library-skills-section", () => ({
  SkillsSection: () =>
    fixtures.populated ? (
      <article>Frontend Skill</article>
    ) : (
      <p>No skills available yet.</p>
    ),
}));

vi.mock("../library-memory-buckets-section", () => ({
  MemoryBucketsSection: () =>
    fixtures.populated ? (
      <article>Godot Engine 4.6</article>
    ) : (
      <p>No memory buckets available yet.</p>
    ),
}));

import { LibraryTab } from "../library-tab";

const fixtures = vi.hoisted(() => ({ populated: false }));

afterEach(() => {
  fixtures.populated = false;
  cleanup();
});

describe("LibraryTab", () => {
  it("renderiza as 3 seções (MCP, Skills, Memory Buckets)", () => {
    render(<LibraryTab threadId="t1" />);
    expect(screen.getByText("MCP", { selector: "span" })).toBeTruthy();
    expect(screen.getByText("Skills", { selector: "span" })).toBeTruthy();
    expect(
      screen.getByText("Memory Buckets", { selector: "span" }),
    ).toBeTruthy();
  });

  it("mantém as três seções fechadas ao iniciar", () => {
    render(<LibraryTab threadId="t1" />);
    for (const label of [
      "No MCP servers available yet.",
      "No skills available yet.",
      "No memory buckets available yet.",
    ]) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
  });

  it("abre uma seção por vez e fecha a anterior", () => {
    render(<LibraryTab threadId="t1" />);
    fireEvent.click(screen.getByText("MCP", { selector: "span" }));
    expect(screen.getByText("No MCP servers available yet.")).toBeTruthy();

    fireEvent.click(screen.getByText("Skills", { selector: "span" }));
    expect(screen.getByText("No skills available yet.")).toBeTruthy();
    expect(
      screen.queryByText("No MCP servers available yet."),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("No memory buckets available yet."),
    ).not.toBeInTheDocument();
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
    expect(
      screen.queryByText("No MCP servers available yet."),
    ).not.toBeInTheDocument();
  });

  it("permite recolher a seção ativa sem ativar outra", () => {
    render(<LibraryTab threadId="t1" />);
    const memoryHeader = screen.getByText("Memory Buckets", {
      selector: "span",
    });
    fireEvent.click(memoryHeader);
    expect(screen.getByText("No memory buckets available yet.")).toBeTruthy();
    fireEvent.click(memoryHeader);
    expect(
      screen.queryByText("No memory buckets available yet."),
    ).not.toBeInTheDocument();
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

  it("renderiza dados representativos em cada seção", () => {
    fixtures.populated = true;
    render(<LibraryTab threadId="t1" />);

    fireEvent.click(screen.getByRole("tab", { name: "MCP" }));
    expect(screen.getByText("Brave Search MCP")).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Skills" }));
    expect(screen.getByText("Frontend Skill")).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    expect(screen.getByText("Godot Engine 4.6")).toBeVisible();
  });

  it("move o foco entre categorias com as setas", () => {
    render(<LibraryTab threadId="t1" />);
    const mcpTab = screen.getByRole("tab", { name: "MCP" });
    const skillsTab = screen.getByRole("tab", { name: "Skills" });
    mcpTab.focus();
    fireEvent.keyDown(mcpTab, { key: "ArrowRight" });
    expect(skillsTab).toHaveFocus();
    expect(skillsTab).toHaveAttribute("aria-selected", "true");
  });

  it("busca filtra o campo de texto sem quebrar com resultado vazio", () => {
    render(<LibraryTab threadId="t1" />);
    const input = screen.getByPlaceholderText("Search the Library…");
    fireEvent.change(input, { target: { value: "algo que não existe" } });
    // seções continuam montadas (vazias já eram, filtro não quebra nada)
    expect(screen.getByText("MCP", { selector: "span" })).toBeTruthy();
  });
});
