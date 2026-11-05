// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { SegmentedControl } from "../segmented-control";

afterEach(() => {
  cleanup();
});

const options = [
  { id: "system" as const, label: "Sistema" },
  { id: "light" as const, label: "Claro" },
  { id: "dark" as const, label: "Escuro" },
];

describe("SegmentedControl", () => {
  it("renderiza todas as opções", () => {
    render(
      <SegmentedControl value="dark" onChange={vi.fn()} options={options} />,
    );
    for (const opt of options) {
      expect(screen.getByText(opt.label)).toBeInTheDocument();
    }
  });

  it("marca aria-pressed=true só na opção ativa", () => {
    render(
      <SegmentedControl value="dark" onChange={vi.fn()} options={options} />,
    );
    expect(screen.getByText("Sistema")).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByText("Escuro")).toHaveAttribute("aria-pressed", "true");
  });

  it("clicar numa opção chama onChange com o id dela", () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl value="system" onChange={onChange} options={options} />,
    );

    fireEvent.click(screen.getByText("Claro"));

    expect(onChange).toHaveBeenCalledWith("light");
  });

  it("erro/borda — disabled bloqueia o clique, onChange nunca é chamado", () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        value="system"
        onChange={onChange}
        options={options}
        disabled
      />,
    );

    fireEvent.click(screen.getByText("Claro"));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText("Claro").closest("button")).toBeDisabled();
  });
});
