/**
 * components/workbench/file-viewer.tsx
 * Cobre getMediaKind (detecção por extensão) e rawFileUrl (construção de URL).
 * Não monta componentes React — só testa as funções puras exportadas.
 */

import { describe, it, expect } from "vitest";
import { getMediaKind, rawFileUrl } from "../components/workbench/file-viewer";

describe("getMediaKind", () => {
  // imagens
  it.each(["png", "jpg", "jpeg", "gif", "webp", "avif", "bmp", "ico", "svg"])(
    "%s → image",
    (ext) => expect(getMediaKind(`file.${ext}`)).toBe("image"),
  );

  // vídeo
  it.each(["mp4", "webm", "mov", "mkv", "m4v", "ogv"])("%s → video", (ext) =>
    expect(getMediaKind(`clip.${ext}`)).toBe("video"),
  );

  // áudio
  it.each(["mp3", "wav", "ogg", "flac", "m4a", "aac"])("%s → audio", (ext) =>
    expect(getMediaKind(`song.${ext}`)).toBe("audio"),
  );

  // pdf
  it("pdf → pdf", () => expect(getMediaKind("doc.pdf")).toBe("pdf"));

  // texto / binário → null
  it.each(["ts", "tsx", "py", "md", "json", "yaml", "txt", "sh", "wasm"])(
    "%s → null",
    (ext) => expect(getMediaKind(`file.${ext}`)).toBeNull(),
  );

  // extensão em maiúsculas é reconhecida
  it("extensão maiúscula é detectada (PNG)", () =>
    expect(getMediaKind("PHOTO.PNG")).toBe("image"));

  // sem extensão → null
  it("sem extensão → null", () => expect(getMediaKind("Makefile")).toBeNull());

  // path com subdiretórios
  it("path profundo detecta pela última extensão", () =>
    expect(getMediaKind("assets/images/logo.svg")).toBe("image"));
});

describe("rawFileUrl", () => {
  it("gera URL com workspaceId e path codificados", () => {
    const url = rawFileUrl("ws-abc", "src/main.ts");
    expect(url).toContain("/workspaces/ws-abc/fs/raw");
    expect(url).toContain("path=src%2Fmain.ts");
  });

  it("workspaceId com caracteres especiais é encodado", () => {
    const url = rawFileUrl("ws/especial", "file.ts");
    expect(url).toContain(encodeURIComponent("ws/especial"));
  });

  it("path com espaço é encodado", () => {
    const url = rawFileUrl("ws1", "meu arquivo.png");
    expect(url).toContain("meu+arquivo.png");
  });
});
