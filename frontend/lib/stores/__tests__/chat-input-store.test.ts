/**
 * Tests para o `chat-input-store`: injeção cross-component (push/consume) e
 * rascunhos por thread persistidos.
 */

import { describe, expect, it, beforeEach } from "vitest";
import { useChatInputStore } from "../chat-input-store";

beforeEach(() => {
  useChatInputStore.setState({ draft: null, mention: null, drafts: {} });
  if (typeof localStorage !== "undefined") localStorage.clear();
});

describe("chat-input-store — injeção cross-component", () => {
  it("pushDraft/consumeDraft preenchem e limpam o draft volátil", () => {
    const s = useChatInputStore.getState();
    s.pushDraft("crie um plano");
    expect(useChatInputStore.getState().draft).toBe("crie um plano");
    useChatInputStore.getState().consumeDraft();
    expect(useChatInputStore.getState().draft).toBeNull();
  });

  it("pushMention/consumeMention preenchem e limpam a mention", () => {
    useChatInputStore.getState().pushMention("src/app.ts");
    expect(useChatInputStore.getState().mention).toBe("src/app.ts");
    useChatInputStore.getState().consumeMention();
    expect(useChatInputStore.getState().mention).toBeNull();
  });

  it("pushDraft sobrescreve o draft anterior", () => {
    const s = useChatInputStore.getState();
    s.pushDraft("primeiro");
    s.pushDraft("segundo");
    expect(useChatInputStore.getState().draft).toBe("segundo");
  });

  it("pushDraft aceita string vazia", () => {
    useChatInputStore.getState().pushDraft("");
    expect(useChatInputStore.getState().draft).toBe("");
  });

  it("pushMention sobrescreve a mention anterior", () => {
    const s = useChatInputStore.getState();
    s.pushMention("a.ts");
    s.pushMention("b.ts");
    expect(useChatInputStore.getState().mention).toBe("b.ts");
  });

  it("consumeDraft com draft já nulo é no-op", () => {
    useChatInputStore.getState().consumeDraft();
    expect(useChatInputStore.getState().draft).toBeNull();
  });

  it("draft e mention são independentes", () => {
    const s = useChatInputStore.getState();
    s.pushDraft("texto");
    s.pushMention("path");
    s.consumeDraft();
    expect(useChatInputStore.getState().draft).toBeNull();
    expect(useChatInputStore.getState().mention).toBe("path");
  });
});

describe("chat-input-store — rascunhos por thread", () => {
  it("getDraft devolve string vazia para thread sem rascunho", () => {
    expect(useChatInputStore.getState().getDraft("t1")).toBe("");
  });

  it("setDraft persiste por thread e isola threads diferentes", () => {
    const s = useChatInputStore.getState();
    s.setDraft("t1", "olá A");
    s.setDraft("t2", "olá B");
    expect(useChatInputStore.getState().getDraft("t1")).toBe("olá A");
    expect(useChatInputStore.getState().getDraft("t2")).toBe("olá B");
  });

  it("setDraft com texto vazio remove a entrada (não acumula vazios)", () => {
    const s = useChatInputStore.getState();
    s.setDraft("t1", "rascunho");
    s.setDraft("t1", "");
    expect("t1" in useChatInputStore.getState().drafts).toBe(false);
  });

  it("setDraft idempotente: mesmo valor não cria novo objeto de estado", () => {
    const s = useChatInputStore.getState();
    s.setDraft("t1", "x");
    const before = useChatInputStore.getState().drafts;
    s.setDraft("t1", "x");
    expect(useChatInputStore.getState().drafts).toBe(before);
  });

  it("clearDraft remove o rascunho da thread (envio)", () => {
    const s = useChatInputStore.getState();
    s.setDraft("t1", "vai ser enviada");
    s.clearDraft("t1");
    expect(useChatInputStore.getState().getDraft("t1")).toBe("");
  });

  it("clearDraft em thread inexistente é no-op", () => {
    const before = useChatInputStore.getState().drafts;
    useChatInputStore.getState().clearDraft("inexistente");
    expect(useChatInputStore.getState().drafts).toBe(before);
  });

  it("setDraft sobrescreve o rascunho da mesma thread", () => {
    const s = useChatInputStore.getState();
    s.setDraft("t1", "antigo");
    s.setDraft("t1", "novo");
    expect(useChatInputStore.getState().getDraft("t1")).toBe("novo");
  });

  it("setDraft vazio em thread inexistente é no-op (mesmo estado)", () => {
    const before = useChatInputStore.getState().drafts;
    useChatInputStore.getState().setDraft("ghost", "");
    expect(useChatInputStore.getState().drafts).toBe(before);
  });

  it("setDraft preserva os rascunhos das outras threads", () => {
    const s = useChatInputStore.getState();
    s.setDraft("t1", "A");
    s.setDraft("t2", "B");
    s.setDraft("t1", "A2");
    expect(useChatInputStore.getState().getDraft("t2")).toBe("B");
  });

  it("clearDraft de uma thread não afeta as outras", () => {
    const s = useChatInputStore.getState();
    s.setDraft("t1", "A");
    s.setDraft("t2", "B");
    s.clearDraft("t1");
    expect(useChatInputStore.getState().getDraft("t1")).toBe("");
    expect(useChatInputStore.getState().getDraft("t2")).toBe("B");
  });

  it("suporta múltiplos rascunhos simultâneos", () => {
    const s = useChatInputStore.getState();
    for (let i = 0; i < 5; i++) s.setDraft(`t${i}`, `draft ${i}`);
    expect(Object.keys(useChatInputStore.getState().drafts)).toHaveLength(5);
    expect(useChatInputStore.getState().getDraft("t3")).toBe("draft 3");
  });

  it("getDraft volta a vazio após clearDraft", () => {
    const s = useChatInputStore.getState();
    s.setDraft("t1", "algo");
    expect(useChatInputStore.getState().getDraft("t1")).toBe("algo");
    s.clearDraft("t1");
    expect(useChatInputStore.getState().getDraft("t1")).toBe("");
  });

  it("setDraft vazio remove só a thread alvo", () => {
    const s = useChatInputStore.getState();
    s.setDraft("t1", "A");
    s.setDraft("t2", "B");
    s.setDraft("t1", "");
    expect("t1" in useChatInputStore.getState().drafts).toBe(false);
    expect(useChatInputStore.getState().getDraft("t2")).toBe("B");
  });
});
