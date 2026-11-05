import { describe, it, expect } from "vitest";
import {
  getModelPrice,
  estimateCost,
  formatCost,
} from "@/lib/config/model-prices";

describe("getModelPrice", () => {
  it("retorna preços positivos para um modelo conhecido", () => {
    const p = getModelPrice("gemini-2.5-flash");
    expect(p.input).toBeGreaterThan(0);
    expect(p.output).toBeGreaterThan(0);
  });

  it("é case-insensitive no id do modelo", () => {
    expect(getModelPrice("GEMINI-2.5-FLASH")).toEqual(
      getModelPrice("gemini-2.5-flash"),
    );
  });

  it("modelo totalmente desconhecido cai no fallback {1, 5}", () => {
    expect(getModelPrice("modelo-inexistente-zzz")).toEqual({
      input: 1.0,
      output: 5.0,
    });
  });
});

describe("estimateCost", () => {
  it("zero tokens → custo zero", () => {
    expect(estimateCost("gemini-2.5-flash", 0, 0)).toBe(0);
  });

  it("escala linearmente com os tokens", () => {
    const base = estimateCost("gemini-2.5-flash", 1000, 500);
    const dobro = estimateCost("gemini-2.5-flash", 2000, 1000);
    expect(dobro).toBeCloseTo(base * 2, 10);
  });

  it("usa input e output separadamente (fallback 1/5 por 1M)", () => {
    // modelo desconhecido → {input:1, output:5}; (1e6*1 + 1e6*5)/1e6 = 6
    expect(estimateCost("zzz-unknown", 1_000_000, 1_000_000)).toBeCloseTo(
      6,
      10,
    );
  });
});

describe("formatCost", () => {
  it("zero ou negativo → string vazia", () => {
    expect(formatCost(0)).toBe("");
    expect(formatCost(-1)).toBe("");
  });

  it("< $0.001 → '<$0.001'", () => {
    expect(formatCost(0.0005)).toBe("<$0.001");
  });

  it("< $0.01 → 4 casas", () => {
    expect(formatCost(0.005)).toBe("$0.0050");
  });

  it("< $1 → 3 casas", () => {
    expect(formatCost(0.5)).toBe("$0.500");
  });

  it("≥ $1 → 2 casas", () => {
    expect(formatCost(2)).toBe("$2.00");
  });
});
