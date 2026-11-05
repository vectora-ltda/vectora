/**
 * Implementação do módulo virtual `next/image` (resolvido pelo
 * `resolve.alias` do `vite.config.ts`). Renderiza `<img>` nativo.
 *
 * Aceita `src` (string ou `{src, width?, height?}`), `alt`, `width`,
 * `height`, `priority`, `fill`, `sizes`. Os atributos `quality`,
 * `placeholder`, `blurDataURL`, `unoptimized`, `loader` aparecem na
 * assinatura mas não têm efeito.
 *
 * `fill=true` posiciona absolute + object-fit: cover; ignora
 * `width`/`height` nesse caso.
 */

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
  loader?: unknown;
}

export default function Image({
  src,
  alt,
  width,
  height,
  priority,
  fill,
  quality: _quality,
  placeholder: _placeholder,
  blurDataURL: _blurDataURL,
  unoptimized: _unoptimized,
  loader: _loader,
  sizes,
  style,
  ...rest
}: NextImageProps) {
  const resolvedSrc = typeof src === "string" ? src : src.src;
  const fillStyle = fill
    ? {
        position: "absolute" as const,
        inset: 0,
        width: "100%",
        height: "100%",
        objectFit: "cover" as const,
        ...style,
      }
    : style;
  return (
    <img
      src={resolvedSrc}
      alt={alt}
      width={fill ? undefined : width}
      height={fill ? undefined : height}
      loading={priority ? "eager" : "lazy"}
      decoding="async"
      sizes={sizes}
      style={fillStyle}
      {...rest}
    />
  );
}
