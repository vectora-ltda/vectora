/**
 * Helpers do medidor de contexto (lib/utils/tokens.ts).
 *
 * estimateTokens: heurística ~4 chars/token.
 * formatTokens: formato compacto "164.8k" / "950".
 */

import { describe, it, expect } from "vitest";
import { estimateTokens, formatTokens } from "../lib/utils/tokens";

describe("estimateTokens", () => {
  it("retorna 0 para string vazia", () => {
    expect(estimateTokens("")).toBe(0);
  });

  it("estima ~1 token a cada 4 caracteres", () => {
    expect(estimateTokens("abcd")).toBe(1);
    expect(estimateTokens("abcdefgh")).toBe(2);
  });

  it("arredonda para cima caracteres parciais", () => {
    expect(estimateTokens("abcde")).toBe(2); // 5/4 = 1.25 → 2
  });

  it("soma um array de textos", () => {
    expect(estimateTokens(["abcd", "abcd"])).toBe(2);
  });
});

describe("formatTokens", () => {
  it("mostra números pequenos como inteiros", () => {
    expect(formatTokens(950)).toBe("950");
  });

  it("usa sufixo k com uma casa decimal", () => {
    expect(formatTokens(164_800)).toBe("164.8k");
  });

  it("remove .0 redundante", () => {
    expect(formatTokens(200_000)).toBe("200k");
  });
});
