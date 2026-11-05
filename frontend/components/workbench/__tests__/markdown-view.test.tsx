// @vitest-environment jsdom
import { describe, expect, it, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import { MarkdownView } from "../markdown-view";

afterEach(() => {
  cleanup();
});

describe("MarkdownView", () => {
  it("renderiza markdown válido com heading, lista e código", () => {
    render(
      <MarkdownView
        content={
          "# Título\n\n- item um\n- item dois\n\n```js\nconst x = 1;\n```"
        }
      />,
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Título" }),
    ).toBeTruthy();
    expect(screen.getByText("item um")).toBeTruthy();
    expect(screen.getByText("item dois")).toBeTruthy();
    expect(screen.getByText("const x = 1;")).toBeTruthy();
  });

  it("aplica classes prose-hN de escala de heading conforme CSS do container", () => {
    const { container } = render(
      <MarkdownView content="# H1\n## H2\n### H3" />,
    );
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.className).toContain("prose-h1:text-lg");
    expect(wrapper.className).toContain("prose-h2:text-base");
    expect(wrapper.className).toContain("prose-h3:text-sm");
  });

  it("suporta tabelas e strikethrough via remark-gfm", () => {
    render(
      <MarkdownView
        content={"| a | b |\n| - | - |\n| 1 | 2 |\n\n~~riscado~~"}
      />,
    );
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByText("riscado").tagName).toBe("DEL");
  });

  it("markdown vazio não quebra e renderiza container sem conteúdo textual", () => {
    const { container } = render(<MarkdownView content="" />);
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper).toBeTruthy();
    expect(wrapper.textContent).toBe("");
  });

  it("markdown malformado (colchetes/links quebrados) não lança exceção e ainda mostra o texto", () => {
    expect(() =>
      render(<MarkdownView content={"[link quebrado(sem fechamento"} />),
    ).not.toThrow();
    expect(screen.getByText(/link quebrado/)).toBeTruthy();
  });
});
