/**
 * Tests para toApiAttachments: derivação de AttachmentKind por mimetype.
 */

import { describe, expect, it } from "vitest";
import { toApiAttachments } from "@/lib/utils/chat/attachments";
import type { ImageAttachment } from "@/lib/types";

function attachment(overrides: Partial<ImageAttachment>): ImageAttachment {
  return {
    id: "1",
    base64: "ZmFrZQ==",
    url: "blob:fake",
    mimeType: "text/plain",
    name: "file.txt",
    size: 10,
    ...overrides,
  };
}

describe("toApiAttachments", () => {
  it("deriva kind=audio para arquivos audio/*", () => {
    const [result] = toApiAttachments([
      attachment({ mimeType: "audio/mpeg", name: "memo.mp3" }),
    ]);
    expect(result.kind).toBe("audio");
  });

  it("deriva kind=code para mimetypes desconhecidos (fallback)", () => {
    const [result] = toApiAttachments([
      attachment({ mimeType: "application/x-unknown", name: "data.bin" }),
    ]);
    expect(result.kind).toBe("code");
  });
});
