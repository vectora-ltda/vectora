import { useEffect, useRef, useState } from "react";

/**
 * Largura real do elemento via ResizeObserver — mais confiável que
 * breakpoint de viewport pra um grupo que encolhe dentro de um header
 * flexível (a largura disponível depende de quem mais está no header,
 * não da janela inteira).
 */
export function useElementWidth<T extends HTMLElement>(): [
  React.RefObject<T | null>,
  number,
] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    setWidth(node.getBoundingClientRect().width);
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return [ref, width];
}
