/**
 * files-utils — funções puras: normalização de path, tom de badge git, formatação de data.
 */

import { describe, it, expect } from "vitest";

import { GIT_BADGE_TONE, norm, fmtDate } from "../files-utils";

describe("norm", () => {
  it("converte separadores Windows para POSIX", () => {
    expect(norm("src\\lib\\file.ts")).toBe("src/lib/file.ts");
  });

  it("mantém path já em POSIX inalterado; string vazia retorna vazia", () => {
    expect(norm("src/lib/file.ts")).toBe("src/lib/file.ts");
    expect(norm("")).toBe("");
  });

  it("converte múltiplas barras invertidas consecutivas", () => {
    expect(norm("a\\\\b\\c")).toBe("a//b/c");
  });
});

describe("fmtDate", () => {
  it("formata data ISO 8601 em dd/mm/yyyy", () => {
    expect(fmtDate("2026-01-05T10:30:00Z")).toBe("05/01/2026");
  });

  it("preenche dia e mês com zero à esquerda quando necessário", () => {
    expect(fmtDate("2026-03-09T12:00:00Z")).toBe("09/03/2026");
  });

  it("string inválida/malformada cai no fallback dos primeiros 10 caracteres, sem NaN", () => {
    expect(fmtDate("not-a-date")).toBe("not-a-date");
  });

  it("string vazia cai no fallback sem lançar", () => {
    expect(fmtDate("")).toBe("");
  });
});

describe("GIT_BADGE_TONE", () => {
  it("mapeia cada status conhecido (M/A/D/R/?) para uma classe de cor", () => {
    expect(GIT_BADGE_TONE.M).toBe("text-amber-500");
    expect(GIT_BADGE_TONE.A).toBe("text-green-500");
    expect(GIT_BADGE_TONE.D).toBe("text-destructive");
    expect(GIT_BADGE_TONE.R).toBe("text-blue-400");
    expect(GIT_BADGE_TONE["?"]).toBe("text-muted-foreground");
  });

  it("status desconhecido não está no mapa (undefined) — chamador deve ter fallback", () => {
    expect(GIT_BADGE_TONE.Z).toBeUndefined();
  });
});
