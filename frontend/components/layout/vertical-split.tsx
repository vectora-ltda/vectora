"use client";

/**
 * VerticalSplit — split vertical minimalista (topo flexível, base com altura
 * controlada em px). Espelha `horizontal-split.tsx`, trocando o eixo: o handle
 * arrasta `bottomSize` (px) entre `minBottom` e `maxBottom` com `row-resize`.
 *
 * Usado na aba Files do workbench para separar a árvore (topo, `flex-1`) do
 * viewer de arquivo (base, altura arrastável). Quando `showBottom=false`, o
 * topo ocupa 100% — sem faixa residual.
 */

import { useCallback, useEffect, useRef, type ReactNode } from "react";

interface VerticalSplitProps {
  top: ReactNode;
  bottom: ReactNode | null;
  showBottom: boolean;
  /** Altura do painel inferior em px. Atualizada durante o drag. */
  bottomSize: number;
  onResize: (size: number) => void;
  /** Altura mínima do painel inferior em px. */
  minBottom?: number;
  /** Altura máxima do painel inferior em px. */
  maxBottom?: number;
  className?: string;
}

export function VerticalSplit({
  top,
  bottom,
  showBottom,
  bottomSize,
  onResize,
  minBottom = 120,
  maxBottom = 900,
  className,
}: VerticalSplitProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    draggingRef.current = true;
    (e.target as Element).setPointerCapture?.(e.pointerId);
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
  }, []);

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingRef.current) return;
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const distFromBottom = rect.bottom - e.clientY;
      // Não deixa o topo sumir: cap dinâmico em ~70% da altura do container.
      const dynamicMax = Math.min(maxBottom, rect.height - 80);
      const clamped = Math.max(minBottom, Math.min(dynamicMax, distFromBottom));
      onResize(clamped);
    },
    [maxBottom, minBottom, onResize],
  );

  const onPointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    (e.target as Element).releasePointerCapture?.(e.pointerId);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }, []);

  // Restaura cursor mesmo se desmontar no meio do drag.
  useEffect(() => {
    return () => {
      if (draggingRef.current) {
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      }
    };
  }, []);

  const bottomHeight = Math.max(minBottom, Math.min(maxBottom, bottomSize));

  return (
    <div
      ref={containerRef}
      className={`flex flex-col min-h-0 ${className ?? ""}`}
    >
      <div className="flex-1 min-h-0 overflow-hidden">{top}</div>
      {showBottom && (
        <>
          <div
            role="separator"
            aria-orientation="horizontal"
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            className="h-1 bg-border/40 hover:bg-border transition-colors cursor-row-resize shrink-0"
          />
          <div
            style={{ height: bottomHeight }}
            className="shrink-0 overflow-hidden"
          >
            {bottom}
          </div>
        </>
      )}
    </div>
  );
}
