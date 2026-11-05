/**
 * Tests de `client-config`: resolução/criação de ClientProfile. Cada caminho
 * feliz tem o par de borda — input vazio, label/id só com espaços, cor vazia —
 * que DEVE cair no fallback/derivação, nunca vazar valor inválido (CLAUDE.md
 * §18).
 */

import { describe, expect, it } from "vitest";
import { resolveClientProfile, createClientProfile } from "../client-config";

const DEFAULT_COLOR = "#6366f1";

describe("client-config — resolveClientProfile", () => {
  it("sem input devolve o fallback local completo", () => {
    expect(resolveClientProfile()).toEqual({
      id: "local-client",
      label: "Local Session",
      avatarColor: DEFAULT_COLOR,
    });
  });

  it("input vazio: usa o id fallback e DERIVA o label dos últimos 4 chars", () => {
    // `{}` é truthy → não cai no fallback completo; o label é derivado do id.
    const p = resolveClientProfile({});
    expect(p.id).toBe("local-client");
    expect(p.label).toBe("Client IENT"); // "local-client".slice(-4) → "IENT"
    expect(p.avatarColor).toBe(DEFAULT_COLOR);
  });

  it("label com espaços nas pontas é trimado", () => {
    expect(resolveClientProfile({ label: "  My Client  " }).label).toBe(
      "My Client",
    );
  });

  it("avatarColor explícito é respeitado", () => {
    expect(resolveClientProfile({ avatarColor: "#abcdef" }).avatarColor).toBe(
      "#abcdef",
    );
  });

  // ── borda/erro: valores inválidos NÃO podem vazar ──
  it("label só com espaços cai no label derivado do id", () => {
    expect(resolveClientProfile({ id: "abcd1234", label: "   " }).label).toBe(
      "Client 1234",
    );
  });

  it("id só com espaços cai no id fallback", () => {
    expect(resolveClientProfile({ id: "   " }).id).toBe("local-client");
  });

  it("avatarColor vazio cai na cor default", () => {
    expect(resolveClientProfile({ avatarColor: "" }).avatarColor).toBe(
      DEFAULT_COLOR,
    );
  });

  it("a derivação do label faz uppercase do sufixo", () => {
    expect(resolveClientProfile({ id: "xxxxab12" }).label).toBe("Client AB12");
  });
});

describe("client-config — createClientProfile", () => {
  it("sem overrides gera um id e um label derivado (Client XXXX)", () => {
    const p = createClientProfile();
    expect(p.id).toBeTruthy();
    expect(p.label).toMatch(/^Client [0-9A-Z]{4}$/);
    expect(p.avatarColor).toBe(DEFAULT_COLOR);
  });

  it("respeita overrides completos", () => {
    expect(
      createClientProfile({ id: "fixed", label: "L", avatarColor: "#000000" }),
    ).toEqual({ id: "fixed", label: "L", avatarColor: "#000000" });
  });

  it("override só de id deriva o label e usa a cor default", () => {
    const p = createClientProfile({ id: "deadbeef" });
    expect(p.id).toBe("deadbeef");
    expect(p.label).toBe("Client BEEF");
    expect(p.avatarColor).toBe(DEFAULT_COLOR);
  });
});
