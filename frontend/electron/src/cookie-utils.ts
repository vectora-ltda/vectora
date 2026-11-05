/**
 * Utilitários puros para manipulação de cookies no proxy IPC do Electron.
 * Sem dependências de Electron — facilita testes unitários isolados.
 */

export interface ParsedSetCookie {
  name: string;
  value: string;
  attrs: Record<string, string>;
  httpOnly: boolean;
}

/**
 * Parseia um header Set-Cookie em suas partes constituintes.
 * Retorna null se a string não tiver o formato "name=value[; attr...]".
 */
export function parseSetCookieHeader(
  cookieStr: string,
): ParsedSetCookie | null {
  const [nameValuePart, ...attrParts] = cookieStr
    .split(";")
    .map((s) => s.trim());
  const eqIdx = nameValuePart.indexOf("=");
  if (eqIdx === -1) return null;
  const name = nameValuePart.slice(0, eqIdx).trim();
  if (!name) return null;
  const value = nameValuePart.slice(eqIdx + 1).trim();

  let httpOnly = false;
  const attrs: Record<string, string> = {};
  for (const part of attrParts) {
    if (!part) continue;
    if (part.toLowerCase() === "httponly") {
      httpOnly = true;
      continue;
    }
    const eq = part.indexOf("=");
    if (eq !== -1) {
      attrs[part.slice(0, eq).toLowerCase().trim()] = part.slice(eq + 1).trim();
    }
  }

  return { name, value, attrs, httpOnly };
}

/**
 * Constrói o valor do header Cookie a partir do store in-memory.
 * Ex.: "vectora_access=TOKEN1; vectora_refresh=TOKEN2"
 */
export function buildCookieHeader(store: ReadonlyMap<string, string>): string {
  return Array.from(store.entries())
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");
}
