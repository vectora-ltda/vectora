/**
 * useWorkbenchSWR — stale-while-revalidate genérico para o workbench.
 *
 * Padrão: o componente passa uma chave + fetcher + função "tem cache" + função
 * "stale". O hook dispara o fetcher imediatamente se não houver cache; caso
 * contrário, dispara silenciosamente em segundo plano se a entrada estiver
 * stale. O componente lê o cache do store normalmente.
 *
 * Extensões em C.1:
 *   - `ttl` (ms): staleness baseado em tempo desde o último fetch. Defaults
 *     pré-definidos em `SWR_TTL` para os tipos de recurso do workbench.
 *   - Revalidação automática em `visibilitychange`, `focus` e `online`.
 */

import { useCallback, useEffect, useRef } from "react";

/** TTLs canônicos por tipo de recurso (ms). */
export const SWR_TTL = {
  workspaces: 60_000,
  threads: 30_000,
  safeRoots: 5 * 60_000,
  license: 5 * 60_000,
  default: 30_000,
} as const;

interface SWROptions {
  /** Chave única — quando muda, dispara nova validação. */
  key: string;
  /** True se já existe cache renderizável. */
  hasCache: boolean;
  /**
   * True (ou função que devolve true) se a entrada cacheada está obsoleta.
   * Quando `ttl` é fornecido, este campo pode omitir a verificação de tempo
   * (o hook calcula internamente). Aceita função para quem compara contra
   * `Date.now()` — mantém a leitura do relógio fora do corpo de render,
   * avaliada só quando o efeito de revalidação roda.
   */
  isStale: boolean | (() => boolean);
  /** Função que faz o fetch e atualiza o store. */
  revalidate: () => Promise<void> | void;
  /** Pular o hook (ex.: workspace ausente). */
  skip?: boolean;
  /**
   * TTL em ms. Quando > 0 o hook revalida automaticamente ao retornar o foco
   * (visibilitychange/focus) ou recuperar conexão (online), desde que o último
   * fetch tenha ocorrido há mais de `ttl` ms.
   */
  ttl?: number;
}

export function useWorkbenchSWR({
  key,
  hasCache,
  isStale,
  revalidate,
  skip = false,
  ttl = 0,
}: SWROptions) {
  // Deduplicação de validações concorrentes para a mesma chave.
  const inFlight = useRef<Record<string, boolean>>({});
  // Timestamp do último fetch bem-sucedido por chave.
  const lastFetch = useRef<Record<string, number>>({});

  const run = useCallback(
    (force = false) => {
      if (skip) return;
      const stale =
        (typeof isStale === "function" ? isStale() : isStale) ||
        (ttl > 0 ? Date.now() - (lastFetch.current[key] ?? 0) > ttl : false);
      if (!force && hasCache && !stale) return;
      if (inFlight.current[key]) return;
      inFlight.current[key] = true;
      Promise.resolve(revalidate()).finally(() => {
        inFlight.current[key] = false;
        lastFetch.current[key] = Date.now();
      });
    },
    // revalidate é intencionalmente omitido (identidade instável).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, hasCache, isStale, skip, ttl],
  );

  // Revalidação normal (chave / stale flag mudou).
  useEffect(() => {
    run();
  }, [run]);

  // Revalidação por eventos de foco/visibilidade/conectividade.
  useEffect(() => {
    if (skip || ttl <= 0) return;

    const onFocus = () => run();
    const onVisibility = () => {
      if (document.visibilityState === "visible") run();
    };
    const onOnline = () => run();

    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("online", onOnline);

    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("online", onOnline);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skip, ttl]);
}
