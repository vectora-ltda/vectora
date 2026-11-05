import { describe, it, expect } from "vitest";
import { extractTextFromContent } from "../content-helpers";

describe("extractTextFromContent", () => {
  it("content string retorna a própria string", () => {
    expect(extractTextFromContent("olá")).toBe("olá");
  });

  it("array de blocos de texto concatena com separador duplo", () => {
    expect(
      extractTextFromContent([
        { type: "text", text: "primeiro" },
        { type: "text", text: "segundo" },
      ]),
    ).toBe("primeiro\n\nsegundo");
  });

  it("array com strings soltas também é aceito", () => {
    expect(extractTextFromContent(["a", "b"])).toBe("a\n\nb");
  });

  it("array filtra blocos de tipo diferente de 'text' (ex.: image_url)", () => {
    expect(
      extractTextFromContent([
        { type: "text", text: "visível" },
        { type: "image_url", image_url: { url: "x" } },
      ]),
    ).toBe("visível");
  });

  it("bloco de texto sem campo 'text' vira string vazia, sem lançar (erro/borda)", () => {
    expect(() => extractTextFromContent([{ type: "text" }])).not.toThrow();
    expect(extractTextFromContent([{ type: "text" }])).toBe("");
  });

  it("content null/undefined/número retorna string vazia (erro/borda)", () => {
    expect(extractTextFromContent(null)).toBe("");
    expect(extractTextFromContent(undefined)).toBe("");
    expect(extractTextFromContent(42)).toBe("");
  });
});
