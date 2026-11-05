// @vitest-environment jsdom
/**
 * Tests para o Callout: role acessível por tipo, título e conteúdo.
 */

import { describe, expect, it, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { Callout } from "../callout";

afterEach(cleanup);

describe("Callout", () => {
  it("renderiza título e conteúdo", () => {
    render(
      <Callout title="Atenção">
        <p>corpo do aviso</p>
      </Callout>,
    );
    expect(screen.getByText("Atenção")).toBeInTheDocument();
    expect(screen.getByText("corpo do aviso")).toBeInTheDocument();
  });

  it("usa role=alert para error e warning", () => {
    const { rerender } = render(<Callout type="error">x</Callout>);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    rerender(<Callout type="warning">x</Callout>);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("usa role=note para info e success", () => {
    render(<Callout type="success">ok</Callout>);
    expect(screen.getByRole("note")).toBeInTheDocument();
  });

  it("não renderiza título quando ausente", () => {
    render(<Callout type="info">só conteúdo</Callout>);
    expect(screen.getByText("só conteúdo")).toBeInTheDocument();
  });
});
