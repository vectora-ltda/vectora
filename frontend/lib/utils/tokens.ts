/**
 * Estimativa e formatação de tokens para o medidor de contexto (R5).
 *
 * A estimativa é uma heurística client-side (~4 caracteres por token) — boa o
 * suficiente para indicar o quanto da janela de contexto foi consumido sem
 * depender de um tokenizer real no browser.
 */

const CHARS_PER_TOKEN = 4;

/** Estima o número de tokens de um texto (ou da soma de vários). */
export function estimateTokens(input: string | string[]): number {
  const texts = Array.isArray(input) ? input : [input];
  let total = 0;
  for (const text of texts) {
    if (text) total += Math.ceil(text.length / CHARS_PER_TOKEN);
  }
  return total;
}

/** Formata uma contagem de tokens de forma compacta: 950 → "950", 164800 → "164.8k". */
export function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  const k = n / 1000;
  // Uma casa decimal, removendo ".0" redundante (200.0k → 200k).
  return `${k.toFixed(1).replace(/\.0$/, "")}k`;
}
