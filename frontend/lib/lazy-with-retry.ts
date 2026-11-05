import { lazy, type ComponentType, type LazyExoticComponent } from "react";

/**
 * `React.lazy` resiliente a chunk obsoleto.
 *
 * Após um novo build, os chunks ganham hash novo; uma SPA já carregada ainda
 * referencia o hash antigo, então o primeiro `import()` dinâmico falha (404 /
 * "Failed to fetch dynamically imported module") e o erro derruba a rota. Aqui
 * detectamos essa falha e recarregamos a página UMA vez (busca o index.html
 * novo com os hashes atuais) — era o que o usuário fazia manualmente. Uma flag
 * por `key` em sessionStorage evita loop de reload se a falha for real.
 */
export function lazyWithRetry<T extends ComponentType<unknown>>(
  factory: () => Promise<{ default: T }>,
  key: string,
): LazyExoticComponent<T> {
  return lazy(async () => {
    const flag = `vectora-lazy-retry-${key}`;
    try {
      const mod = await factory();
      try {
        sessionStorage.removeItem(flag);
      } catch {
        /* sessionStorage indisponível — segue sem a flag */
      }
      return mod;
    } catch (err) {
      let retried = false;
      try {
        retried = sessionStorage.getItem(flag) === "1";
        if (!retried) sessionStorage.setItem(flag, "1");
      } catch {
        /* sem sessionStorage: não dá para coordenar retry, propaga o erro */
        throw err;
      }
      if (!retried && typeof window !== "undefined") {
        window.location.reload();
        // Promise que nunca resolve — o reload assume o controle.
        return new Promise<{ default: T }>(() => {});
      }
      throw err;
    }
  });
}
