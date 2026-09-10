// @vitest-environment jsdom
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import PageHeader from "./PageHeader";

describe("PageHeader", () => {
  it("renderiza o título", () => {
    render(<PageHeader title="Preços" />);
    expect(screen.getByRole("heading", { name: "Preços" })).toBeInTheDocument();
  });

  it("não renderiza subtítulo quando omitido (edge)", () => {
    render(<PageHeader title="Preços" />);
    expect(screen.queryByText(/./, { selector: "p" })).not.toBeInTheDocument();
  });

  it("renderiza o subtítulo quando informado", () => {
    render(<PageHeader title="Preços" subtitle="Planos simples e diretos" />);
    expect(screen.getByText("Planos simples e diretos")).toBeInTheDocument();
  });

  it("renderiza children", () => {
    render(
      <PageHeader title="Preços">
        <button>Assinar</button>
      </PageHeader>,
    );
    expect(screen.getByRole("button", { name: "Assinar" })).toBeInTheDocument();
  });

  it("usa alinhamento à esquerda quando align='left' (edge)", () => {
    const { container } = render(<PageHeader title="Preços" align="left" />);
    expect(container.firstChild).toHaveClass("items-start", "text-start");
  });
});
