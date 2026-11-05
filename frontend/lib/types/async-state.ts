/**
 * AsyncState — máquina de estado para operações assíncronas em stores Zustand.
 *
 * Substitui o padrão `loading: boolean` (que não distingue "nunca carregado"
 * de "carregando de novo" nem carrega o motivo de uma falha) por uma máquina
 * de 4 estados explícita:
 *
 *   idle → loading → success
 *                  ↘ error
 *
 * Uso típico num store:
 *   status: AsyncStatus;
 *   error: string | null;
 *   fetchedAt: number | null;   // hasLoaded = fetchedAt !== null
 *
 * `hasLoaded` deriva de `fetchedAt` (não de `status`) porque um refresh em
 * background entra em `status: "loading"` sem perder os dados já exibidos —
 * a UI deve continuar mostrando o cache, não um skeleton, nesse caso.
 */

export type AsyncStatus = "idle" | "loading" | "success" | "error";

export interface AsyncSlice {
  status: AsyncStatus;
  error: string | null;
}

/** Estado inicial — nada foi buscado ainda. */
export const ASYNC_IDLE: AsyncSlice = { status: "idle", error: null };

/** Helpers para transições — usar dentro de `set()` nos stores. */
export const asyncLoading = (): AsyncSlice => ({
  status: "loading",
  error: null,
});
export const asyncSuccess = (): AsyncSlice => ({
  status: "success",
  error: null,
});
export const asyncError = (message: string): AsyncSlice => ({
  status: "error",
  error: message,
});

/** `true` quando os dados já foram carregados ao menos uma vez (cache existe). */
export function hasLoaded(fetchedAt: number | null): boolean {
  return fetchedAt !== null;
}

/** Mensagem de erro padrão quando a causa real não pôde ser determinada. */
export const UNKNOWN_ASYNC_ERROR = "Falha inesperada. Tente novamente.";

/**
 * ActionResult<T> — resultado padronizado de ações assíncronas em stores.
 *
 * Substitui `Promise<T | null>` (que não carrega o motivo da falha) por uma
 * discriminated union explícita:
 *
 *   ok: true  → dado disponível em `.data`
 *   ok: false → mensagem de erro em `.error`; campo opcional `.field`
 *               indica qual campo de formulário está associado ao erro.
 *
 * Uso no store:
 *   async create(...): Promise<ActionResult<WorkspaceInfo>> { ... }
 *     return { ok: true, data: workspace };
 *     return { ok: false, error: message };
 *
 * Uso no componente:
 *   const result = await store.create(...);
 *   if (!result.ok) showFieldError(result.field, result.error);
 */
export type ActionResult<T> =
  { ok: true; data: T } | { ok: false; error: string; field?: string };

export function toErrorMessage(
  err: unknown,
  fallback = UNKNOWN_ASYNC_ERROR,
): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === "string" && err) return err;
  return fallback;
}
