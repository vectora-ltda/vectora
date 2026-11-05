/**
 * Cliente de identidade por username — auto-preenchimento a partir do nome
 * + checagem de disponibilidade no backend, usados no wizard de criação de
 * conta (`/auth/signup`).
 */

export interface UsernameStatus {
  /** Forma canônica do que foi consultado (minúsculas, sem acento). */
  normalized: string;
  available: boolean;
  /** Sugestão livre quando em uso (ex.: "bruno#4821"); == normalized se livre. */
  suggestion: string;
}

/**
 * Slug local do nome para auto-preencher o campo username. Espelha o
 * `slugify_username` do backend: minúsculas, sem acento, só `[a-z0-9]`.
 *
 * NFKD decompõe letras acentuadas em base + marca combinante (ex.: "é" →
 * "e" + acento); o filtro final `[^a-z0-9]` remove a marca combinante junto
 * com espaços/pontuação, preservando a letra base. Nome sem caractere
 * aproveitável devolve `""` (o backend cai em `"user"`, mas aqui `""` deixa a
 * UI não mostrar status até o usuário digitar algo).
 */
export function slugifyUsername(name: string): string {
  return name
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

/**
 * Consulta `GET /auth/username-available`. Devolve `null` em falha de rede ou
 * resposta não-ok — o caller trata como "não foi possível checar" (não bloqueia
 * o submit por falha de rede; o backend rejeita duplicado com 409 de qualquer
 * forma).
 */
export async function checkUsername(
  username: string,
): Promise<UsernameStatus | null> {
  try {
    const res = await fetch(
      `/auth/username-available?username=${encodeURIComponent(username)}`,
      { credentials: "include" },
    );
    if (!res.ok) return null;
    return (await res.json()) as UsernameStatus;
  } catch {
    return null;
  }
}
