/**
 * lib/utils/string/string-helpers.ts
 * Cobre truncate, generateQuickTitle e generateThreadTitle.
 */

import { describe, it, expect } from "vitest";
import {
  truncate,
  generateQuickTitle,
  generateThreadTitle,
} from "../lib/utils/string/string-helpers";

describe("truncate", () => {
  it("retorna string original se <= max", () =>
    expect(truncate("hello", 10)).toBe("hello"));
  it("trunca e adiciona '...' quando > max", () =>
    expect(truncate("hello world", 5)).toBe("hello..."));
  it("exatamente no limite → não trunca", () =>
    expect(truncate("abc", 3)).toBe("abc"));
  it("string vazia → permanece vazia", () => expect(truncate("", 5)).toBe(""));
});

describe("generateQuickTitle", () => {
  it("'How do I ...' → 'How to ...'", () => {
    const t = generateQuickTitle("How do I install node?");
    expect(t.startsWith("How to")).toBe(true);
  });

  it("'How to ...' → mantém padrão", () => {
    const t = generateQuickTitle("How to fix this bug");
    expect(t.startsWith("How to")).toBe(true);
  });

  it("'What is ...' → prefixo 'About'", () => {
    const t = generateQuickTitle("What is TypeScript?");
    expect(t.startsWith("About")).toBe(true);
  });

  it("'What are ...' → prefixo 'About'", () => {
    const t = generateQuickTitle("What are hooks?");
    expect(t.startsWith("About")).toBe(true);
  });

  it("'Why ...' → truncado", () => {
    const t = generateQuickTitle("Why is this slow?");
    expect(t.toLowerCase().startsWith("why")).toBe(true);
  });

  it("'error: ...' → prefixo 'Error:'", () => {
    const t = generateQuickTitle("I got an error: cannot find module");
    expect(t.startsWith("Error:")).toBe(true);
  });

  it("mensagem comum → capitaliza primeira letra", () => {
    const t = generateQuickTitle("fix the build pipeline");
    expect(t[0]).toBe("F");
  });

  it("prefixo 'Please ...' é removido", () => {
    const t = generateQuickTitle("Please fix my code");
    expect(t.toLowerCase().startsWith("please")).toBe(false);
  });

  it("prefixo 'Can you ...' é removido", () => {
    const t = generateQuickTitle("Can you help me?");
    expect(t.toLowerCase().startsWith("can you")).toBe(false);
  });

  it("mensagem longa é truncada a ≤ 60 chars (default)", () => {
    const long = "a".repeat(100);
    const t = generateQuickTitle(long);
    expect(t.length).toBeLessThanOrEqual(63); // 60 + "..."
  });
});

describe("generateThreadTitle", () => {
  it("retorna string não-vazia para qualquer mensagem", async () => {
    const t = await generateThreadTitle({ userMessage: "hello" });
    expect(typeof t).toBe("string");
    expect(t.length).toBeGreaterThan(0);
  });

  it("respeita maxLength personalizado", async () => {
    const t = await generateThreadTitle({
      userMessage: "a".repeat(200),
      maxLength: 20,
    });
    expect(t.length).toBeLessThanOrEqual(23); // 20 + "..."
  });
});
