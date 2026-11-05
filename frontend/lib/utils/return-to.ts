/**
 * Preserva o destino de navegação quando a sessão expira no meio do uso.
 *
 * `vectora-client.ts::redirectToLogin` despacha um hard-redirect via
 * `window.location.href = "/auth/signin"` quando o refresh do token falha em
 * uma chamada de API — isso descarta toda a navegação em memória (TanStack
 * Router) e perderia o caminho atual. O guard de `__root.tsx::beforeLoad` já
 * cobre o caso "carregamento inicial sem sessão" via `?from=<path>`; este
 * util cobre o caso complementar "sessão caiu durante o uso".
 *
 * `sessionStorage` (não `localStorage`): o valor só importa para a próxima
 * navegação desta aba — não deve sobreviver indefinidamente nem vazar para
 * outras abas/sessões futuras.
 */

const RETURN_TO_SESSION_STORAGE_SLOT = "vectora:return_to";

function storage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** Chamar imediatamente antes do hard-redirect para `/auth/signin`. */
export function saveReturnTo(path: string): void {
  storage()?.setItem(RETURN_TO_SESSION_STORAGE_SLOT, path);
}

/** Lê e consome (remove) o destino salvo — `null` se não havia nenhum. */
export function consumeReturnTo(): string | null {
  const s = storage();
  if (!s) return null;
  const value = s.getItem(RETURN_TO_SESSION_STORAGE_SLOT);
  if (value) s.removeItem(RETURN_TO_SESSION_STORAGE_SLOT);
  return value;
}
