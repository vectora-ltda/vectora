/**
 * Base URL prefixada em todas as chamadas REST/SSE para o backend.
 *
 * Default ``""`` faz o cliente usar paths relativos no mesmo origin —
 * ``VITE_VECTORA_API_URL`` sobrescreve quando dev ou testes precisam
 * apontar para outro host.
 */

export const VECTORA_API_URL: string =
  (typeof import.meta !== "undefined" &&
    import.meta.env?.VITE_VECTORA_API_URL) ||
  "";
