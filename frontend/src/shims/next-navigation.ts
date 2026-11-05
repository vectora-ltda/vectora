/**
 * Implementação do módulo virtual `next/navigation` (resolvido pelo
 * `resolve.alias` do `vite.config.ts`).
 *
 * Exporta `useRouter`, `usePathname`, `useSearchParams`, `useParams`,
 * `redirect` e `notFound`. Navegação delega ao TanStack Router;
 * `redirect`/`notFound` fazem hard-fail via `window.location` e exceção.
 */

import {
  useNavigate,
  useLocation,
  useParams as useTanstackParams,
} from "@tanstack/react-router";

type Href = string;

interface RouterShim {
  push(href: Href): void;
  replace(href: Href): void;
  back(): void;
  forward(): void;
  refresh(): void;
  prefetch(href: Href): void;
}

export function useRouter(): RouterShim {
  const navigate = useNavigate();
  return {
    // `as never`: `href` chega como string arbitrária — fora do tipo
    // estrito do `routeTree`. Resolução real acontece no runtime do router.
    push: (href) => void navigate({ to: href as never }),
    replace: (href) => void navigate({ to: href as never, replace: true }),
    back: () => window.history.back(),
    forward: () => window.history.forward(),
    refresh: () => window.location.reload(),
    // Prefetch automático via `defaultPreload: "intent"` no router config.
    prefetch: () => undefined,
  };
}

export function usePathname(): string {
  const location = useLocation();
  return location.pathname;
}

export function useSearchParams(): URLSearchParams {
  const location = useLocation();
  // `location.searchStr` existe em runtime mas só em parte das versões do
  // tipo público — cast restrito devolve a forma quando disponível e usa
  // `window.location.search` como fallback.
  const maybeSearchStr = (location as { searchStr?: string }).searchStr;
  if (typeof maybeSearchStr === "string") {
    return new URLSearchParams(maybeSearchStr);
  }
  if (typeof window !== "undefined") {
    return new URLSearchParams(window.location.search);
  }
  return new URLSearchParams();
}

export function useParams<
  T extends Record<string, string> = Record<string, string>,
>(): T {
  // Cast desacopla do shape exato do `options` (varia entre versões do
  // router). Em runtime sempre passamos `{ strict: false }`.
  return (useTanstackParams as unknown as (opts: { strict: false }) => T)({
    strict: false,
  });
}

export function redirect(href: Href): never {
  if (typeof window !== "undefined") {
    window.location.replace(href);
  }
  throw new Error(`redirect: ${href}`);
}

export function notFound(): never {
  throw new Error("notFound()");
}
