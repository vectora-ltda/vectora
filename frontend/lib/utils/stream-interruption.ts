/**
 * Detecção client-side de streaming interrompido.
 *
 * Não há endpoint de backend capaz de inspecionar o checkpoint da sessão
 * e diferenciar "parado em HITL" de "conexão caiu no meio da geração".
 * Em vez disso, marcamos localmente quando um stream começa e desmarcamos
 * quando ele termina por **qualquer** via conhecida (done, hitl, error,
 * abort do usuário) — só sobra marcado o caso em que a aba
 * fechou/recarregou/crashou no meio da resposta.
 *
 * Persistido em `localStorage` (sobrevive a reload/crash; `sessionStorage`
 * sumiria com a aba). Marca expira em 30min para não acusar falso-positivo
 * de uma sessão muito antiga reaberta.
 */

const PREFIX = "vectora:streaming:";
const STALE_AFTER_MS = 30 * 60 * 1000;

function storage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** Chamar ao iniciar um stream (processStream/processResume). */
export function markStreamStarted(threadId: string): void {
  storage()?.setItem(`${PREFIX}${threadId}`, String(Date.now()));
}

/** Chamar no `finally` do stream — cobre done/hitl/error/abort. */
export function markStreamEnded(threadId: string): void {
  storage()?.removeItem(`${PREFIX}${threadId}`);
}

/**
 * Lê e consome (remove) a marca de interrupção para `threadId`.
 * Retorna `true` apenas se havia marca válida (não expirada) — ou seja,
 * a resposta anterior muito provavelmente não chegou a terminar.
 */
export function consumeInterruptedFlag(threadId: string): boolean {
  const s = storage();
  if (!s) return false;
  const key = `${PREFIX}${threadId}`;
  const raw = s.getItem(key);
  if (!raw) return false;
  s.removeItem(key);
  const startedAt = Number(raw);
  return Number.isFinite(startedAt) && Date.now() - startedAt < STALE_AFTER_MS;
}
