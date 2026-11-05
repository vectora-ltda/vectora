import { describe, it, expect } from "vitest";
import { toApiAttachments } from "../attachments";
import type { ImageAttachment } from "@/lib/types";

function attachment(overrides: Partial<ImageAttachment> = {}): ImageAttachment {
  return {
    id: "a1",
    base64: "AAAA",
    url: "blob:x",
    mimeType: "image/png",
    name: "foto.png",
    size: 100,
    ...overrides,
  };
}

describe("toApiAttachments", () => {
  it("converte anexo de imagem completo pro formato da API", () => {
    const [result] = toApiAttachments([attachment()]);

    expect(result).toEqual({
      kind: "image",
      name: "foto.png",
      mime_type: "image/png",
      base64_data: "AAAA",
    });
  });

  it("deriva kind 'pdf' e 'audio' pelo mimeType, e 'code' pra qualquer outro", () => {
    const [pdf, audio, texto] = toApiAttachments([
      attachment({ id: "p", mimeType: "application/pdf", name: "doc.pdf" }),
      attachment({ id: "a", mimeType: "audio/mpeg", name: "audio.mp3" }),
      attachment({ id: "t", mimeType: "text/plain", name: "notas.txt" }),
    ]);

    expect(pdf?.kind).toBe("pdf");
    expect(audio?.kind).toBe("audio");
    expect(texto?.kind).toBe("code");
  });

  it("descarta anexos sem base64 (ainda não carregados) — erro/borda", () => {
    const result = toApiAttachments([
      attachment({ id: "sem-base64", base64: undefined }),
      attachment({ id: "ok" }),
    ]);

    expect(result).toHaveLength(1);
    expect(result[0]?.name).toBe("foto.png");
  });

  it("descarta anexos sem name — erro/borda", () => {
    const result = toApiAttachments([attachment({ name: undefined })]);

    expect(result).toHaveLength(0);
  });

  it("lista vazia retorna lista vazia", () => {
    expect(toApiAttachments([])).toEqual([]);
  });
});
