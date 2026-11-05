/**
 * lib/utils/usage.ts
 * Cobre formatTokens, formatResetIn, usageLevel, usageBarColor.
 */

import { describe, it, expect } from "vitest";
import {
  formatTokens,
  formatResetIn,
  usageLevel,
  usageBarColor,
  CONTEXT_BLOCK_PCT,
  CONTEXT_WARN_PCT,
} from "../lib/utils/usage";

describe("formatTokens", () => {
  it("zero retorna '0'", () => expect(formatTokens(0)).toBe("0"));
  it("negativo retorna '0'", () => expect(formatTokens(-1)).toBe("0"));
  it("NaN retorna '0'", () => expect(formatTokens(NaN)).toBe("0"));
  it("Infinity retorna '0'", () => expect(formatTokens(Infinity)).toBe("0"));
  it("< 1000 retorna número puro", () => expect(formatTokens(999)).toBe("999"));
  it("1000 retorna '1.0k'", () => expect(formatTokens(1000)).toBe("1.0k"));
  it("1234 retorna '1.2k'", () => expect(formatTokens(1234)).toBe("1.2k"));
  it("999999 retorna '1000.0k'", () =>
    expect(formatTokens(999_999)).toBe("1000.0k"));
  it("1000000 retorna '1.0M'", () =>
    expect(formatTokens(1_000_000)).toBe("1.0M"));
  it("1500000 retorna '1.5M'", () =>
    expect(formatTokens(1_500_000)).toBe("1.5M"));
});

describe("formatResetIn", () => {
  it("0 segundos → '0s'", () => expect(formatResetIn(0)).toBe("0s"));
  it("negativo → '0s'", () => expect(formatResetIn(-5)).toBe("0s"));
  it("59s → '59s'", () => expect(formatResetIn(59)).toBe("59s"));
  it("60s → '1m'", () => expect(formatResetIn(60)).toBe("1m"));
  it("90s → '2m'", () => expect(formatResetIn(90)).toBe("2m"));
  it("3599s → '60m'", () => expect(formatResetIn(3599)).toBe("60m"));
  it("3600s → '1h'", () => expect(formatResetIn(3600)).toBe("1h"));
  it("7200s → '2h'", () => expect(formatResetIn(7200)).toBe("2h"));
  it("arredonda parcial: 5400s → '2h'", () =>
    expect(formatResetIn(5400)).toBe("2h"));
});

describe("usageLevel", () => {
  it("< 80 → ok", () => expect(usageLevel(79)).toBe("ok"));
  it("0 → ok", () => expect(usageLevel(0)).toBe("ok"));
  it("80 → warn", () => expect(usageLevel(80)).toBe("warn"));
  it("94 → warn", () => expect(usageLevel(94)).toBe("warn"));
  it("95 → danger", () => expect(usageLevel(95)).toBe("danger"));
  it("100 → danger", () => expect(usageLevel(100)).toBe("danger"));
});

describe("usageBarColor", () => {
  it("ok → emerald", () => expect(usageBarColor("ok")).toContain("emerald"));
  it("warn → amber", () => expect(usageBarColor("warn")).toContain("amber"));
  it("danger → red", () => expect(usageBarColor("danger")).toContain("red"));
});

describe("constantes de threshold", () => {
  it("CONTEXT_BLOCK_PCT é 95", () => expect(CONTEXT_BLOCK_PCT).toBe(95));
  it("CONTEXT_WARN_PCT é 80", () => expect(CONTEXT_WARN_PCT).toBe(80));
});
