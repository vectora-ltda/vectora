/**
 * Implementação do módulo virtual `next/link` (resolvido pelo
 * `resolve.alias` do `vite.config.ts`).
 *
 * Aceita `href`, `prefetch`, `replace`, children. Os atributos
 * `scroll`, `as`, `passHref`, `legacyBehavior`, `locale` são
 * preservados na assinatura mas não têm efeito.
 *
 * URLs externas (http(s):, mailto:, tel:, wa.me) caem para `<a>`
 * nativo — `RouterLink` só conhece rotas internas.
 */

import type { AnchorHTMLAttributes, ReactNode } from "react";
import { Link as RouterLink } from "@tanstack/react-router";

interface NextLinkProps extends Omit<
  AnchorHTMLAttributes<HTMLAnchorElement>,
  "href"
> {
  href: string;
  prefetch?: boolean;
  replace?: boolean;
  scroll?: boolean;
  shallow?: boolean;
  passHref?: boolean;
  legacyBehavior?: boolean;
  locale?: string | false;
  children?: ReactNode;
}

export default function Link({
  href,
  prefetch: _prefetch,
  replace,
  scroll: _scroll,
  shallow: _shallow,
  passHref: _passHref,
  legacyBehavior: _legacyBehavior,
  locale: _locale,
  children,
  ...rest
}: NextLinkProps) {
  // URLs externas (http://, https://, mailto:, tel:) usam <a> nativo.
  const isExternal =
    /^(https?:|mailto:|tel:|wa\.me)/i.test(href) || href.startsWith("//");
  if (isExternal) {
    return (
      <a href={href} {...rest}>
        {children}
      </a>
    );
  }
  // `to` é validado pelo TanStack Router contra o routeTree gerado. O shim
  // recebe paths arbitrários do código herdado do Next.js, então usamos
  // `as never` para silenciar o type-check e delegar a validação ao runtime.
  return (
    <RouterLink to={href as never} replace={replace} {...rest}>
      {children as React.ReactNode}
    </RouterLink>
  );
}
