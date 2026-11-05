/**
 * Tests para stripMarkdownEnvelope.
 *
 * Cobre: envelope completo (N≥3 crases, qualquer N desde que abre/fecha
 * combinem), parcial (streaming), ausente, vazio, CRLF, indentação,
 * conteúdo interno com blocos de código, e casos limites.
 */

import { describe, it, expect } from "vitest";
import { stripMarkdownEnvelope } from "../markdown-envelope";

describe("stripMarkdownEnvelope", () => {
  // ── Caminho feliz ─────────────────────────────────────────────────────────

  it("remove envelope completo e retorna conteúdo interno", () => {
    const input = "``````markdown\nOlá, mundo\n``````";
    expect(stripMarkdownEnvelope(input)).toBe("Olá, mundo");
  });

  it("preserva conteúdo com múltiplas linhas", () => {
    const input = "``````markdown\n# Título\n\nPaágrafo.\n``````";
    expect(stripMarkdownEnvelope(input)).toBe("# Título\n\nPaágrafo.");
  });

  it("preserva blocos de código internos (≤5 crases)", () => {
    const input = "``````markdown\n```python\nprint('hello')\n```\n``````";
    expect(stripMarkdownEnvelope(input)).toBe("```python\nprint('hello')\n```");
  });

  it("remove apenas a abertura quando stream ainda está chegando (parcial)", () => {
    const input = "``````markdown\nTexto parcial ainda chegando";
    expect(stripMarkdownEnvelope(input)).toBe("Texto parcial ainda chegando");
  });

  it("remove abertura + fechamento parcial no final (stream encerrado antes do expected)", () => {
    const input = "``````markdown\nTexto\n``````";
    expect(stripMarkdownEnvelope(input)).toBe("Texto");
  });

  // ── Sem envelope ─────────────────────────────────────────────────────────

  it("retorna texto plain sem alteração", () => {
    const input = "Resposta sem envelope nenhum.";
    expect(stripMarkdownEnvelope(input)).toBe("Resposta sem envelope nenhum.");
  });

  it("retorna markdown sem envelope sem alteração", () => {
    const input = "# Título\n\n- item 1\n- item 2";
    expect(stripMarkdownEnvelope(input)).toBe("# Título\n\n- item 1\n- item 2");
  });

  it("remove envelope de 3 crases (convenção mais comum do modelo, regressão do bug de vazamento)", () => {
    const input = "```markdown\nconteúdo\n```";
    expect(stripMarkdownEnvelope(input)).toBe("conteúdo");
  });

  it("remove envelope de 4 ou 5 crases (qualquer N≥3, desde que abre/fecha combinem)", () => {
    expect(stripMarkdownEnvelope("````markdown\nconteúdo\n````")).toBe(
      "conteúdo",
    );
    expect(stripMarkdownEnvelope("`````markdown\nconteúdo\n`````")).toBe(
      "conteúdo",
    );
  });

  it("abre/fecha com N de crases diferentes: remove só a abertura, mantém o resto (fence malformado, mesmo tratamento de um streaming parcial)", () => {
    const input = "```markdown\nconteúdo\n``````";
    expect(stripMarkdownEnvelope(input)).toBe("conteúdo\n``````");
  });

  // ── Casos vazios e limites ────────────────────────────────────────────────

  it("string vazia retorna string vazia", () => {
    expect(stripMarkdownEnvelope("")).toBe("");
  });

  it("string só com whitespace retorna inalterada", () => {
    expect(stripMarkdownEnvelope("   ")).toBe("   ");
  });

  it("apenas a linha de abertura (sem corpo) retorna string vazia", () => {
    const input = "``````markdown\n";
    expect(stripMarkdownEnvelope(input)).toBe("");
  });

  it("envelope com corpo vazio (duas linhas só com crases) retorna string vazia", () => {
    const input = "``````markdown\n\n``````";
    // FULL_ENVELOPE_RE: corpo é \n — match[1] seria ""
    const result = stripMarkdownEnvelope(input);
    expect(typeof result).toBe("string");
    expect(result.trim()).toBe("");
  });

  // ── Tolerância de formato ─────────────────────────────────────────────────

  it("tolera espaço após `markdown` na abertura", () => {
    const input = "``````markdown   \nConteúdo\n``````";
    expect(stripMarkdownEnvelope(input)).toBe("Conteúdo");
  });

  it("tolera CRLF no final de linha da abertura", () => {
    const input = "``````markdown\r\nConteúdo\r\n``````";
    expect(stripMarkdownEnvelope(input)).toBe("Conteúdo");
  });

  it("tolera whitespace no início (trimStart antes do match)", () => {
    const input = "  ``````markdown\nConteúdo\n``````";
    expect(stripMarkdownEnvelope(input)).toBe("Conteúdo");
  });

  it("tolera espaço após o fechamento ``````", () => {
    const input = "``````markdown\nConteúdo\n``````  ";
    expect(stripMarkdownEnvelope(input)).toBe("Conteúdo");
  });

  // ── Casos onde NÃO deve remover ───────────────────────────────────────────

  it("envelope que começa depois de outro texto NÃO é removido", () => {
    const input = "Texto pré-existente\n``````markdown\nCorpo\n``````";
    // Não começa no início → não é o envelope esperado
    expect(stripMarkdownEnvelope(input)).toBe(
      "Texto pré-existente\n``````markdown\nCorpo\n``````",
    );
  });

  it("conteúdo que contém 6 crases mas sem `markdown` não é removido", () => {
    const input = "``````python\nprint('hello')\n``````";
    expect(stripMarkdownEnvelope(input)).toBe(
      "``````python\nprint('hello')\n``````",
    );
  });

  // ── Streaming realista ────────────────────────────────────────────────────

  it("token a token: apenas abertura → vai chegando conteúdo", () => {
    // Simula estado parcial após cada token
    expect(stripMarkdownEnvelope("``````markdown\n")).toBe("");
    expect(stripMarkdownEnvelope("``````markdown\nOlá")).toBe("Olá");
    expect(stripMarkdownEnvelope("``````markdown\nOlá, mundo")).toBe(
      "Olá, mundo",
    );
    expect(stripMarkdownEnvelope("``````markdown\nOlá, mundo\n``````")).toBe(
      "Olá, mundo",
    );
  });

  it("conteúdo com bloco de código de 5 crases não quebra o envelope", () => {
    const input = "``````markdown\n`````python\nprint('hi')\n`````\n``````";
    expect(stripMarkdownEnvelope(input)).toBe(
      "`````python\nprint('hi')\n`````",
    );
  });

  it("token a token com envelope de 3 crases (caso real mais comum na prática)", () => {
    expect(stripMarkdownEnvelope("```markdown\n")).toBe("");
    expect(stripMarkdownEnvelope("```markdown\nOlá")).toBe("Olá");
    expect(stripMarkdownEnvelope("```markdown\nOlá, mundo\n```")).toBe(
      "Olá, mundo",
    );
  });

  it("message_break: dois segmentos — primeiro é stripped, segundo é plain", () => {
    // Primeiro segmento (antes do message_break) — chega com envelope
    const seg1 = "``````markdown\nPrimeiro segmento.\n``````";
    expect(stripMarkdownEnvelope(seg1)).toBe("Primeiro segmento.");

    // Segundo segmento (após message_break) — pode ser plain
    const seg2 = "Segundo segmento sem envelope.";
    expect(stripMarkdownEnvelope(seg2)).toBe("Segundo segmento sem envelope.");
  });
});
