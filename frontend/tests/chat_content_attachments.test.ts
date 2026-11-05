/**
 * Tests para utils puros de conteúdo/attachments do chat.
 */

import { describe, expect, it } from "vitest";
import { extractTextFromContent } from "@/lib/utils/chat/content-helpers";
import { toApiAttachments } from "@/lib/utils/chat/attachments";
import type { ImageAttachment } from "@/lib/types";

describe("extractTextFromContent", () => {
  it("devolve a própria string", () => {
    expect(extractTextFromContent("olá")).toBe("olá");
  });

  it("concatena blocos de texto de um array", () => {
    const content = [
      { type: "text", text: "parte 1" },
      { type: "image", url: "x" },
      { type: "text", text: "parte 2" },
    ];
    expect(extractTextFromContent(content)).toBe("parte 1\n\nparte 2");
  });

  it("aceita strings cruas dentro do array", () => {
    expect(extractTextFromContent(["a", "b"])).toBe("a\n\nb");
  });

  it("devolve string vazia para tipos não suportados", () => {
    expect(extractTextFromContent({ foo: 1 })).toBe("");
    expect(extractTextFromContent(null)).toBe("");
  });
});

function att(over: Partial<ImageAttachment>): ImageAttachment {
  return {
    name: "f",
    base64: "ABC",
    mimeType: "image/png",
    ...over,
  } as unknown as ImageAttachment;
}

describe("toApiAttachments", () => {
  it("deriva kind por mime type (image/pdf/code)", () => {
    const out = toApiAttachments([
      att({ name: "a.png", mimeType: "image/png" }),
      att({ name: "b.pdf", mimeType: "application/pdf" }),
      att({ name: "c.ts", mimeType: "text/typescript" }),
    ]);
    expect(out.map((a) => a.kind)).toEqual(["image", "pdf", "code"]);
    expect(out[0].base64_data).toBe("ABC");
    expect(out[0].mime_type).toBe("image/png");
  });

  it("ignora arquivos sem base64 ou sem nome", () => {
    const out = toApiAttachments([
      att({ name: "ok.png" }),
      att({ name: "semBase", base64: undefined }),
      att({ name: undefined }),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].name).toBe("ok.png");
  });
});
