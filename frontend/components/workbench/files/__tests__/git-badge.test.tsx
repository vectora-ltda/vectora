// @vitest-environment jsdom
/**
 * GitBadge — renderiza (ou não) o status git de um arquivo com a cor certa.
 */

import { describe, it, expect, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";

import { GitBadge } from "../git-badge";

afterEach(() => {
  cleanup();
});

describe("GitBadge", () => {
  it("sem status: não renderiza nada", () => {
    const { container } = render(<GitBadge />);
    expect(container).toBeEmptyDOMElement();
  });

  it("status modificado (M): renderiza a letra com a classe âmbar", () => {
    const { getByTitle } = render(<GitBadge status="M" />);
    const el = getByTitle("M");
    expect(el.textContent).toBe("M");
    expect(el.className).toContain("text-amber-500");
  });

  it("status adicionado (A): renderiza com classe verde", () => {
    const { getByTitle } = render(<GitBadge status="A" />);
    expect(getByTitle("A").className).toContain("text-green-500");
  });

  it("status deletado (D): renderiza com classe destructive", () => {
    const { getByTitle } = render(<GitBadge status="D" />);
    expect(getByTitle("D").className).toContain("text-destructive");
  });

  it("status desconhecido/malformado: cai no fallback text-muted-foreground em vez de quebrar", () => {
    const { getByTitle } = render(<GitBadge status="Z" />);
    expect(getByTitle("Z").className).toContain("text-muted-foreground");
  });

  it("string vazia é falsy: não renderiza (mesmo comportamento de undefined)", () => {
    const { container } = render(<GitBadge status="" />);
    expect(container).toBeEmptyDOMElement();
  });
});
