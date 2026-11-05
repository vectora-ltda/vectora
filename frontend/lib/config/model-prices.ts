/**
 * Tabela de preços de modelos por 1M tokens (USD).
 *
 * Valores aproximados de mercado — usados apenas para exibição indicativa no
 * seletor de modelo e no badge de custo por mensagem. Não usados para
 * cobrança real.
 *
 * Formato: `{ [model_id_prefix]: { input: $/1M, output: $/1M } }`
 * O match é por prefixo (mais específico primeiro).
 */
export interface ModelPrice {
  /** Custo por 1M tokens de entrada (USD). */
  input: number;
  /** Custo por 1M tokens de saída (USD). */
  output: number;
}

const PRICE_TABLE: Array<[string, ModelPrice]> = [
  // Claude 3.5 / 3.7 family
  ["claude-3-7-sonnet", { input: 3.0, output: 15.0 }],
  ["claude-3-5-sonnet", { input: 3.0, output: 15.0 }],
  ["claude-3-5-haiku", { input: 0.8, output: 4.0 }],
  ["claude-3-opus", { input: 15.0, output: 75.0 }],
  ["claude-3-sonnet", { input: 3.0, output: 15.0 }],
  ["claude-3-haiku", { input: 0.25, output: 1.25 }],
  // GPT-4o family
  ["gpt-4o-mini", { input: 0.15, output: 0.6 }],
  ["gpt-4o", { input: 5.0, output: 15.0 }],
  ["gpt-4-turbo", { input: 10.0, output: 30.0 }],
  ["gpt-4", { input: 30.0, output: 60.0 }],
  ["gpt-3.5", { input: 0.5, output: 1.5 }],
  // Gemini
  ["gemini-2.0-flash", { input: 0.1, output: 0.4 }],
  ["gemini-1.5-pro", { input: 3.5, output: 10.5 }],
  ["gemini-1.5-flash", { input: 0.075, output: 0.3 }],
  // Fallback
  ["", { input: 1.0, output: 5.0 }],
];

/**
 * Retorna o preço por 1M tokens para um modelo dado, pelo prefixo mais longo
 * encontrado na tabela. Retorna o fallback `{ input: 1, output: 5 }` quando
 * nenhum prefixo bate.
 */
export function getModelPrice(modelId: string): ModelPrice {
  const id = modelId.toLowerCase();
  for (const [prefix, price] of PRICE_TABLE) {
    if (prefix === "" || id.includes(prefix)) {
      return price;
    }
  }
  return { input: 1.0, output: 5.0 };
}

/**
 * Estima o custo em USD de uma chamada com base em tokens de entrada/saída.
 *
 * @param modelId  — identificador do modelo
 * @param inputTokens  — tokens de entrada
 * @param outputTokens — tokens de saída
 * @returns custo estimado em USD
 */
export function estimateCost(
  modelId: string,
  inputTokens: number,
  outputTokens: number,
): number {
  const price = getModelPrice(modelId);
  return (inputTokens * price.input + outputTokens * price.output) / 1_000_000;
}

/**
 * Formata um custo em USD para exibição compacta.
 *
 * < $0.001  → "<$0.001"
 * < $0.01   → "$0.00X"
 * ≥ $1      → "$X.XX"
 */
export function formatCost(usd: number): string {
  if (usd <= 0) return "";
  if (usd < 0.001) return "<$0.001";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  if (usd < 1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}
