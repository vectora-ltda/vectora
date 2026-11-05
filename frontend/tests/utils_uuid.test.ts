/**
 * lib/utils/uuid.ts
 * Verifica formato RFC 4122 v4 e unicidade.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { safeRandomUUID } from "../lib/utils/uuid";

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("safeRandomUUID — crypto.randomUUID disponível", () => {
  it("retorna string no formato UUID v4", () => {
    expect(safeRandomUUID()).toMatch(UUID_RE);
  });

  it("cada chamada retorna valor único", () => {
    const ids = Array.from({ length: 100 }, () => safeRandomUUID());
    expect(new Set(ids).size).toBe(100);
  });
});

describe("safeRandomUUID — fallback getRandomValues (sem randomUUID)", () => {
  it("gera string com 5 segmentos separados por hífen", () => {
    const originalGetRandomValues = crypto.getRandomValues.bind(crypto);
    vi.stubGlobal("crypto", { getRandomValues: originalGetRandomValues });
    const id = safeRandomUUID();
    expect(id.split("-").length).toBe(5);
  });

  it("gera valores únicos via fallback", () => {
    const originalGetRandomValues = crypto.getRandomValues.bind(crypto);
    vi.stubGlobal("crypto", { getRandomValues: originalGetRandomValues });
    const ids = Array.from({ length: 50 }, () => safeRandomUUID());
    expect(new Set(ids).size).toBe(50);
  });
});

describe("safeRandomUUID — fallback Math.random (sem crypto)", () => {
  it("gera string com 5 segmentos separados por hífen", () => {
    vi.stubGlobal("crypto", undefined);
    const id = safeRandomUUID();
    expect(id.split("-").length).toBe(5);
  });
});
