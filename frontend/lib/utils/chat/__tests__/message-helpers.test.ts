import { describe, it, expect } from "vitest";
import { historyMessageToMessage } from "../message-helpers";
import type { HistoryMessage } from "../../../api/vectora-client";

describe("historyMessageToMessage", () => {
  it("converte uma mensagem humana simples sem anexos", () => {
    const hist: HistoryMessage = {
      role: "human",
      content: "oi",
      created_at: "2026-01-01T00:00:00Z",
      checkpoint_id: "cp1",
    };

    const msg = historyMessageToMessage(hist, "id-1");

    expect(msg.id).toBe("id-1");
    expect(msg.role).toBe("user");
    expect(msg.content).toBe("oi");
    expect(msg.checkpointId).toBe("cp1");
    expect(msg.images).toBeUndefined();
  });

  it("propaga is_error do histórico persistido para isError", () => {
    const failed = historyMessageToMessage(
      { role: "assistant", content: "[falha] provider", is_error: true },
      "id-err",
    );
    expect(failed.isError).toBe(true);

    // false e null (estado desconhecido) nunca marcam a mensagem como erro.
    for (const is_error of [false, null, undefined]) {
      const ok = historyMessageToMessage(
        { role: "assistant", content: "ok", is_error },
        "id-ok",
      );
      expect(ok.isError).toBeUndefined();
    }
  });

  it("mapeia role 'assistant' corretamente", () => {
    const hist: HistoryMessage = { role: "assistant", content: "olá!" };

    const msg = historyMessageToMessage(hist, "id-2");

    expect(msg.role).toBe("assistant");
  });

  it("popula images a partir de attachments com kind=image e url", () => {
    const hist: HistoryMessage = {
      role: "human",
      content: "veja essa imagem",
      attachments: [
        {
          name: "screenshot.png",
          mimeType: "image/png",
          kind: "image",
          size: 2048,
          url: "/threads/t1/attachments/abc.png",
        },
      ],
    };

    const msg = historyMessageToMessage(hist, "id-3");

    expect(msg.images).toHaveLength(1);
    expect(msg.images?.[0]).toEqual({
      id: "/threads/t1/attachments/abc.png",
      url: "/threads/t1/attachments/abc.png",
      mimeType: "image/png",
      name: "screenshot.png",
      size: 2048,
    });
  });

  it("attachment de imagem sem url (falha ao persistir) não vira image — mensagem continua exibível", () => {
    const hist: HistoryMessage = {
      role: "human",
      content: "veja essa imagem",
      attachments: [
        {
          name: "screenshot.png",
          mimeType: "image/png",
          kind: "image",
          size: 2048,
          url: null,
        },
      ],
    };

    const msg = historyMessageToMessage(hist, "id-4");

    expect(msg.images).toBeUndefined();
    expect(msg.content).toBe("veja essa imagem");
  });

  it("attachment que não é imagem (ex: pdf/code) nunca vira image", () => {
    const hist: HistoryMessage = {
      role: "human",
      content: "leia esse arquivo",
      attachments: [
        {
          name: "doc.pdf",
          mimeType: "application/pdf",
          kind: "pdf",
          size: 1024,
          url: "/threads/t1/attachments/doc.pdf",
        },
      ],
    };

    const msg = historyMessageToMessage(hist, "id-5");

    expect(msg.images).toBeUndefined();
  });

  it("múltiplos anexos de imagem viram múltiplas entradas em images", () => {
    const hist: HistoryMessage = {
      role: "human",
      content: "compare essas duas",
      attachments: [
        {
          name: "a.png",
          mimeType: "image/png",
          kind: "image",
          size: 10,
          url: "/threads/t1/attachments/a.png",
        },
        {
          name: "b.png",
          mimeType: "image/png",
          kind: "image",
          size: 20,
          url: "/threads/t1/attachments/b.png",
        },
      ],
    };

    const msg = historyMessageToMessage(hist, "id-6");

    expect(msg.images).toHaveLength(2);
    expect(msg.images?.map((i) => i.name)).toEqual(["a.png", "b.png"]);
  });

  it("sem created_at usa a hora atual como timestamp", () => {
    const hist: HistoryMessage = { role: "human", content: "oi" };

    const before = Date.now();
    const msg = historyMessageToMessage(hist, "id-7");
    const after = Date.now();

    expect(msg.timestamp.getTime()).toBeGreaterThanOrEqual(before);
    expect(msg.timestamp.getTime()).toBeLessThanOrEqual(after);
  });
});
