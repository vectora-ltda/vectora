// @vitest-environment jsdom
/**
 * Tests para saveReturnTo/consumeReturnTo: preserva o destino quando a sessão
 * cai durante o uso (sessionStorage, consumido na próxima navegação).
 */

import { describe, expect, it, beforeEach } from "vitest";
import { saveReturnTo, consumeReturnTo } from "@/lib/utils/return-to";

beforeEach(() => sessionStorage.clear());

describe("return-to", () => {
  it("salva e consome o destino (roundtrip)", () => {
    saveReturnTo("/session/abc");
    expect(consumeReturnTo()).toBe("/session/abc");
  });

  it("consome de forma destrutiva — segunda leitura é null", () => {
    saveReturnTo("/x");
    consumeReturnTo();
    expect(consumeReturnTo()).toBeNull();
  });

  it("retorna null quando nada foi salvo", () => {
    expect(consumeReturnTo()).toBeNull();
  });
});
