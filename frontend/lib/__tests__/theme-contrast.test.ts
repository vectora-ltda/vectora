/**
 * Regressão de legibilidade dos temas claro e escuro ("Light" / "Dark")
 * e contraste estrutural da variável --sidebar.
 *
 * Parseia o styles.css real para travar os requisitos diretamente nos valores
 * publicados — se alguém mudar uma cor sem passar no teste, o CI bloqueia.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, it, expect } from "vitest";

const css = readFileSync(
  path.resolve(process.cwd(), "src/styles.css"),
  "utf-8",
);

// ── Helpers ─────────────────────────────────────────────────────────────────

/** Extrai o valor hex de um token dentro do bloco `.light`. */
function lightToken(name: string): string {
  const start = css.indexOf(".light {");
  const block = css.slice(start, css.indexOf("}", start));
  const m = block.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`token --${name} não encontrado no bloco .light`);
  return m[1];
}

/** Extrai o valor hex de um token dentro do bloco `:root` (tema escuro). */
function darkToken(name: string): string {
  const start = css.indexOf(":root {");
  const end = css.indexOf("}", start);
  const block = css.slice(start, end);
  const m = block.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`token --${name} não encontrado no bloco :root`);
  return m[1];
}

function luminance(hex: string): number {
  const ch = [1, 3, 5].map((i) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}

function contrastRatio(fg: string, bg: string): number {
  const l1 = luminance(fg);
  const l2 = luminance(bg);
  const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
  return (hi + 0.05) / (lo + 0.05);
}

// ── Tema claro ───────────────────────────────────────────────────────────────

describe("tema claro — legibilidade (Light)", () => {
  it("background do tema claro é branco", () => {
    expect(lightToken("background").toLowerCase()).toBe("#ffffff");
  });

  it("foreground tem contraste AAA (>=7) sobre o background", () => {
    const c = contrastRatio(lightToken("foreground"), lightToken("background"));
    expect(c).toBeGreaterThanOrEqual(7);
  });

  it("muted-foreground passa o mínimo AA (>=4.5)", () => {
    const c = contrastRatio(
      lightToken("muted-foreground"),
      lightToken("background"),
    );
    expect(c).toBeGreaterThanOrEqual(4.5);
  });

  it("muted-foreground foi reforçado para >=6 (legibilidade)", () => {
    const c = contrastRatio(
      lightToken("muted-foreground"),
      lightToken("background"),
    );
    expect(c).toBeGreaterThanOrEqual(6);
  });

  it("card-foreground tem contraste AAA sobre o card", () => {
    const c = contrastRatio(lightToken("card-foreground"), lightToken("card"));
    expect(c).toBeGreaterThanOrEqual(7);
  });

  it("destructive-foreground é legível sobre destructive", () => {
    const c = contrastRatio(
      lightToken("destructive-foreground"),
      lightToken("destructive"),
    );
    expect(c).toBeGreaterThanOrEqual(4.5);
  });

  it("primary-foreground é legível sobre primary (CTA)", () => {
    const c = contrastRatio(
      lightToken("primary-foreground"),
      lightToken("primary"),
    );
    expect(c).toBeGreaterThanOrEqual(7);
  });
});

// ── Tema escuro ──────────────────────────────────────────────────────────────

describe("tema escuro — legibilidade (Dark)", () => {
  it("background do tema escuro é definido", () => {
    expect(() => darkToken("background")).not.toThrow();
  });

  it("foreground tem contraste AAA (>=7) sobre o background", () => {
    const c = contrastRatio(darkToken("foreground"), darkToken("background"));
    expect(c).toBeGreaterThanOrEqual(7);
  });

  it("muted-foreground passa o mínimo AA (>=4.5)", () => {
    const c = contrastRatio(
      darkToken("muted-foreground"),
      darkToken("background"),
    );
    expect(c).toBeGreaterThanOrEqual(4.5);
  });

  it("card-foreground tem contraste AAA sobre o card", () => {
    const c = contrastRatio(darkToken("card-foreground"), darkToken("card"));
    expect(c).toBeGreaterThanOrEqual(7);
  });

  it("destructive-foreground é legível sobre destructive", () => {
    const c = contrastRatio(
      darkToken("destructive-foreground"),
      darkToken("destructive"),
    );
    expect(c).toBeGreaterThanOrEqual(4.5);
  });

  it("primary-foreground é legível sobre primary (CTA)", () => {
    const c = contrastRatio(
      darkToken("primary-foreground"),
      darkToken("primary"),
    );
    expect(c).toBeGreaterThanOrEqual(7);
  });
});

// ── --sidebar — contraste estrutural vs --background ────────────────────────
//
// --sidebar não é texto sobre fundo — é a cor de um painel inteiro.
// O requisito não é contraste WCAG de texto (>=4.5), mas sim diferença
// perceptível suficiente para criar a hierarquia visual sidebar/editor.
// Um ratio >=1.05 já é detectável pelo olho humano numa tela calibrada.

describe("--sidebar — contraste estrutural com --background", () => {
  it("dark: --sidebar está definido no bloco :root", () => {
    expect(() => darkToken("sidebar")).not.toThrow();
  });

  it("dark: --sidebar é mais escuro que --background", () => {
    const sidebarL = luminance(darkToken("sidebar"));
    const bgL = luminance(darkToken("background"));
    expect(sidebarL).toBeLessThan(bgL);
  });

  it("dark: --sidebar tem diferença perceptível vs --background (ratio >=1.05)", () => {
    const c = contrastRatio(darkToken("sidebar"), darkToken("background"));
    expect(c).toBeGreaterThanOrEqual(1.05);
  });

  it("dark: --sidebar não é idêntico ao --card (mantém tokens distintos)", () => {
    expect(darkToken("sidebar").toLowerCase()).not.toBe(
      darkToken("card").toLowerCase(),
    );
  });

  it("light: --sidebar está definido no bloco .light", () => {
    expect(() => lightToken("sidebar")).not.toThrow();
  });

  it("light: --sidebar é mais escuro que --background", () => {
    const sidebarL = luminance(lightToken("sidebar"));
    const bgL = luminance(lightToken("background"));
    expect(sidebarL).toBeLessThan(bgL);
  });

  it("light: --sidebar tem diferença perceptível vs --background (ratio >=1.05)", () => {
    const c = contrastRatio(lightToken("sidebar"), lightToken("background"));
    expect(c).toBeGreaterThanOrEqual(1.05);
  });

  it("light: --sidebar não é branco puro (deve ter contraste real)", () => {
    expect(lightToken("sidebar").toLowerCase()).not.toBe("#ffffff");
  });
});
