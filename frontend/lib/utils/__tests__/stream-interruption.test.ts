// @vitest-environment jsdom
/**
 * `stream-interruption.ts` — usado em todo `processStream`/`processResume`
 * pra distinguir "aba fechou/crashou no meio da resposta" de "stream
 * terminou normalmente". Achado da auditoria: módulo central
 * pro fluxo de chat sem nenhum teste próprio.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  markStreamStarted,
  markStreamEnded,
  consumeInterruptedFlag,
} from "../stream-interruption";

describe("stream-interruption", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("marca início e consumeInterruptedFlag retorna true (stream nunca foi encerrado)", () => {
    markStreamStarted("t1");

    expect(consumeInterruptedFlag("t1")).toBe(true);
  });

  it("markStreamEnded remove a marca — consumeInterruptedFlag passa a retornar false", () => {
    markStreamStarted("t1");
    markStreamEnded("t1");

    expect(consumeInterruptedFlag("t1")).toBe(false);
  });

  it("thread sem marca nenhuma retorna false (nunca houve stream em andamento)", () => {
    expect(consumeInterruptedFlag("thread-nunca-usada")).toBe(false);
  });

  it("consumeInterruptedFlag é destrutivo — uma segunda chamada retorna false", () => {
    markStreamStarted("t1");

    expect(consumeInterruptedFlag("t1")).toBe(true);
    expect(consumeInterruptedFlag("t1")).toBe(false);
  });

  it("marca expirada (>30min) é tratada como false e removida (edge — sessão antiga reaberta)", () => {
    const now = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(now - 31 * 60 * 1000);
    markStreamStarted("t1");
    vi.spyOn(Date, "now").mockReturnValue(now);

    expect(consumeInterruptedFlag("t1")).toBe(false);
    // Consumida (removida) mesmo expirada — não deve continuar acusando
    // false-positivo indefinidamente numa releitura futura.
    vi.restoreAllMocks();
    expect(window.localStorage.getItem("vectora:streaming:t1")).toBeNull();
  });

  it("marca com valor corrompido (não-numérico) no storage não lança e é tratada como falsa", () => {
    window.localStorage.setItem("vectora:streaming:t1", "não-é-um-timestamp");

    expect(() => consumeInterruptedFlag("t1")).not.toThrow();
    expect(consumeInterruptedFlag("t1")).toBe(false);
  });

  it("threads diferentes têm marcas isoladas", () => {
    markStreamStarted("t1");

    expect(consumeInterruptedFlag("t2")).toBe(false);
    // t1 continua marcada — consumeInterruptedFlag("t2") não deve ter
    // afetado a marca de t1.
    expect(consumeInterruptedFlag("t1")).toBe(true);
  });
});
