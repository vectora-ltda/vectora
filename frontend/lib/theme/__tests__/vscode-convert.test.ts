import { describe, expect, it } from "vitest";
import { convertVscodeColorTheme, isLightTheme } from "../vscode-convert";

const darkFixture = {
  name: "Fixture Dark",
  colors: {
    "editor.background": "#1e1e1e",
    "editor.foreground": "#d4d4d4",
    "editorWidget.background": "#252526",
    "editorWidget.border": "#454545",
    "button.background": "#0e639c",
    "list.hoverBackground": "#2a2d2e",
    "input.background": "#3c3c3c",
    "sideBar.background": "#181818",
    focusBorder: "#007fd4",
  },
};

const lightFixture = {
  name: "Fixture Light",
  colors: {
    "editor.background": "#ffffff",
    "editor.foreground": "#000000",
    "sideBar.background": "#f3f3f3",
    "activityBar.background": "#2c2c2c",
  },
};

describe("convertVscodeColorTheme", () => {
  it("mapeia um tema dark completo pros 9 campos de BaseThemeColors", () => {
    const result = convertVscodeColorTheme(darkFixture);

    expect(result.background).toBe("#1e1e1e");
    expect(result.foreground).toBe("#d4d4d4");
    expect(result.card).toBe("#252526");
    expect(result.border).toBe("#454545");
    expect(result.primary).toBe("#0e639c");
    expect(result.accent).toBe("#2a2d2e");
    expect(result.muted).toBe("#3c3c3c");
    expect(result.sidebar).toBe("#181818");
    expect(result.userBubble).toBe("#0e639c");
  });

  it("cai no fallback (foreground) quando uma chave preferida está ausente", () => {
    const result = convertVscodeColorTheme(lightFixture);

    // sideBar.background existe → sidebar E card (2ª fonte de card) usam ele.
    expect(result.sidebar).toBe("#f3f3f3");
    expect(result.card).toBe("#f3f3f3");
    // Nenhuma chave de "border" existe → cai no fallback (foreground).
    expect(result.border).toBe("#000000");
  });

  it("normaliza cores com canal alfa (8 dígitos) pra #rrggbb, descartando o alfa", () => {
    const withAlpha = {
      colors: {
        "editor.background": "#1e1e1eff",
        "editor.foreground": "#d4d4d4ff",
        "list.hoverBackground": "#2a2d2e80",
        "editor.selectionBackground": "#3a3d3e40",
      },
    };
    const result = convertVscodeColorTheme(withAlpha);

    expect(result.background).toBe("#1e1e1e");
    expect(result.foreground).toBe("#d4d4d4");
    // accent lê list.hoverBackground primeiro — 8 dígitos vira 6, sem alfa.
    expect(result.accent).toBe("#2a2d2e");
    expect(result.accent).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("expande formas curtas (#rgb/#rgba) pro equivalente de 6 dígitos", () => {
    const shortHex = {
      colors: {
        "editor.background": "#000",
        "editor.foreground": "#fffe",
      },
    };
    const result = convertVscodeColorTheme(shortHex);

    expect(result.background).toBe("#000000");
    expect(result.foreground).toBe("#ffffff");
  });

  it("erro/borda — lança quando faltam editor.background/editor.foreground", () => {
    expect(() => convertVscodeColorTheme({ colors: {} })).toThrow();
    expect(() =>
      convertVscodeColorTheme({ colors: { "editor.background": "#000" } }),
    ).toThrow();
    expect(() => convertVscodeColorTheme({})).toThrow();
    expect(() => convertVscodeColorTheme(null)).toThrow();
  });

  it("erro/borda — valores de cor não-string (número/objeto/null) não quebram, caem no fallback", () => {
    const malformed = {
      colors: {
        "editor.background": "#1e1e1e",
        "editor.foreground": "#d4d4d4",
        "button.background": 42,
        "list.hoverBackground": { r: 1, g: 2, b: 3 },
        "editorWidget.border": null,
      },
    };

    expect(() => convertVscodeColorTheme(malformed)).not.toThrow();
    const result = convertVscodeColorTheme(malformed);
    // Nenhuma das chaves malformadas foi usada — cai no fallback (foreground).
    expect(result.primary).toBe("#d4d4d4");
    expect(result.accent).toBe("#d4d4d4");
    expect(result.border).toBe("#d4d4d4");
  });

  it("erro/borda — editor.background/foreground não-string também são tratados como ausentes", () => {
    expect(() =>
      convertVscodeColorTheme({
        colors: { "editor.background": 123, "editor.foreground": "#fff" },
      }),
    ).toThrow();
  });
});

describe("isLightTheme", () => {
  it("identifica um fundo claro como tema claro", () => {
    const result = convertVscodeColorTheme(lightFixture);
    expect(isLightTheme(result)).toBe(true);
  });

  it("identifica um fundo escuro como tema escuro", () => {
    const result = convertVscodeColorTheme(darkFixture);
    expect(isLightTheme(result)).toBe(false);
  });
});
