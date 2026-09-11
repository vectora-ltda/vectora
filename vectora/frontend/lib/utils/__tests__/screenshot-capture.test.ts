// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { screenshotBytesToFile } from "../screenshot-capture";

describe("screenshotBytesToFile", () => {
  it("retorna null quando o diálogo é cancelado", () => {
    expect(screenshotBytesToFile(null)).toBeNull();
  });

  it("cria um anexo PNG determinístico para bytes selecionados", async () => {
    const file = screenshotBytesToFile(new Uint8Array([137, 80, 78, 71]), 42);
    expect(file).toBeInstanceOf(File);
    expect(file?.name).toBe("screenshot-42.png");
    expect(file?.type).toBe("image/png");
    expect([...new Uint8Array(await file!.arrayBuffer())]).toEqual([
      137, 80, 78, 71,
    ]);
  });
});
