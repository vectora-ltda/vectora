import { describe, expect, it } from "vitest";
import { parseVscodeThemeJson } from "../vscode-install";

describe("parseVscodeThemeJson", () => {
  it("parseia JSON estrito normalmente", () => {
    expect(
      parseVscodeThemeJson('{"colors":{"editor.background":"#000"}}'),
    ).toEqual({
      colors: { "editor.background": "#000" },
    });
  });

  it("tolera comentários de linha e de bloco (JSONC)", () => {
    const contents = `{
      // comentário de linha
      "colors": {
        /* comentário de bloco */
        "editor.background": "#000"
      }
    }`;
    expect(parseVscodeThemeJson(contents)).toEqual({
      colors: { "editor.background": "#000" },
    });
  });

  it("tolera vírgula final (trailing comma)", () => {
    const contents = `{
      "colors": {
        "editor.background": "#000",
        "editor.foreground": "#fff",
      },
    }`;
    expect(parseVscodeThemeJson(contents)).toEqual({
      colors: { "editor.background": "#000", "editor.foreground": "#fff" },
    });
  });

  it("erro/borda — JSON genuinamente malformado ainda lança erro claro", () => {
    expect(() => parseVscodeThemeJson("{ colors: ")).toThrow(/JSON inválido/i);
    expect(() => parseVscodeThemeJson("")).toThrow(/JSON inválido/i);
  });
});
