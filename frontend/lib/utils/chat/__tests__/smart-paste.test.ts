import { describe, expect, it } from "vitest";

import { classifySmartPaste } from "../smart-paste";

describe("classifySmartPaste", () => {
  it("only treats an isolated http URL as a URL", () => {
    expect(classifySmartPaste(" https://example.com/docs ").kind).toBe("url");
    expect(classifySmartPaste("see https://example.com/docs").kind).not.toBe(
      "url",
    );
  });

  it("recognizes JSON and safe YAML collections", () => {
    expect(classifySmartPaste('{"ok":true}')).toMatchObject({
      kind: "json",
      extension: "json",
    });
    expect(classifySmartPaste("name: vectora\nitems:\n  - chat")).toMatchObject(
      {
        kind: "yaml",
        extension: "yaml",
      },
    );
  });

  it("falls back to text for invalid structured data", () => {
    expect(classifySmartPaste("name: [").kind).toBe("text");
  });

  it("classifies HTML as an HTML attachment", () => {
    expect(classifySmartPaste("<section><h1>Relatório</h1></section>")).toEqual(
      {
        kind: "html",
        extension: "html",
        mimeType: "text/html",
      },
    );
  });

  it("keeps prose as plain text", () => {
    expect(classifySmartPaste("Relatório de uso do workspace")).toEqual({
      kind: "text",
      extension: "txt",
      mimeType: "text/plain",
    });
  });
});
