/**
 * Tipos ambient dos módulos virtuais `next/link`, `next/image`,
 * `next/navigation` resolvidos pelo `resolve.alias` do
 * `vite.config.ts` para os arquivos em `chat/src/shims/`.
 *
 * Invariante: este arquivo NÃO pode ter `import`/`export` no top
 * level — qualquer um transforma o `.d.ts` em módulo e os
 * `declare module` deixam de ser ambient.
 */

declare module "next/link" {
  import type { AnchorHTMLAttributes, ReactNode } from "react";
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
  const Link: (props: NextLinkProps) => JSX.Element;
  export default Link;
}

declare module "next/image" {
  import type { ImgHTMLAttributes } from "react";
  interface NextImageProps extends Omit<
    ImgHTMLAttributes<HTMLImageElement>,
    "src" | "alt" | "width" | "height"
  > {
    src: string | { src: string; width?: number; height?: number };
    alt: string;
    width?: number | string;
    height?: number | string;
    priority?: boolean;
    unoptimized?: boolean;
    fill?: boolean;
    quality?: number;
    placeholder?: "blur" | "empty";
    blurDataURL?: string;
    sizes?: string;
  }
  const Image: (props: NextImageProps) => JSX.Element;
  export default Image;
}

declare module "next/navigation" {
  export function useRouter(): {
    push(href: string): void;
    replace(href: string): void;
    back(): void;
    forward(): void;
    refresh(): void;
    prefetch(href: string): void;
  };
  export function usePathname(): string;
  export function useSearchParams(): URLSearchParams;
  export function useParams<
    T extends Record<string, string> = Record<string, string>,
  >(): T;
  export function redirect(href: string): never;
  export function notFound(): never;
}
