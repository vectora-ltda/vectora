/**
 * Tests para message-helpers: criação e manipulação de mensagens no chat.
 * Lógica pura usada no hot path do streaming.
 */

import { describe, expect, it } from "vitest";
import {
  generateMessageId,
  createUserMessage,
  updateMessageInList,
  ensureMessageExists,
} from "@/lib/utils/chat/message-helpers";
import type { Message } from "@/lib/types";

function m(id: string, content = "x"): Message {
  return { id, role: "assistant", content } as unknown as Message;
}

describe("message-helpers", () => {
  it("generateMessageId devolve ids únicos e crescentes", () => {
    const a = generateMessageId();
    const b = generateMessageId();
    expect(a).not.toBe(b);
    // formato timestamp-counter
    expect(a).toMatch(/^\d+-\d+$/);
  });

  it("createUserMessage cria mensagem de usuário com id e timestamp", () => {
    const msg = createUserMessage("olá");
    expect(msg.role).toBe("user");
    expect(msg.content).toBe("olá");
    expect(msg.id).toBeTruthy();
    expect(msg.timestamp).toBeInstanceOf(Date);
  });

  it("updateMessageInList aplica patch objeto só na mensagem certa", () => {
    const list = [m("a", "1"), m("b", "2")];
    const next = updateMessageInList(list, "b", { content: "novo" });
    expect(next.find((x) => x.id === "b")?.content).toBe("novo");
    expect(next.find((x) => x.id === "a")?.content).toBe("1");
  });

  it("updateMessageInList aceita updater como função", () => {
    const list = [m("a", "abc")];
    const next = updateMessageInList(list, "a", (cur) => ({
      content: cur.content + "!",
    }));
    expect(next[0].content).toBe("abc!");
  });

  it("updateMessageInList não muda nada se o id não existe", () => {
    const list = [m("a")];
    const next = updateMessageInList(list, "z", { content: "x" });
    expect(next).toEqual(list);
  });

  it("ensureMessageExists adiciona quando ausente e mantém quando presente", () => {
    const list = [m("a")];
    const added = ensureMessageExists(list, "b", m("b"));
    expect(added).toHaveLength(2);
    const same = ensureMessageExists(list, "a", m("a"));
    expect(same).toBe(list); // referência preservada
  });
});
