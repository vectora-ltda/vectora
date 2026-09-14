"use client";

/**
 * HorizontalSplit — split horizontal minimalista para o layout chat/workbench.
 *
 * Substitui `react-resizable-panels` (v4) nesse caso de uso específico.
 * A lib causava `Symbol.iterator undefined` ao reconciliar quando o painel
 * direito alternava entre visível/oculto e quando a hidratação do Zustand
 * persist tornava os defaults instáveis. Para 2 painéis + handle, o custo
 * de manter a lib não compensa.
 *
 * Características:
 * - Painel esquerdo (`left`): ocupa o espaço restante via `flex: 1`.
 * - Painel direito (`right`): largura controlada por `rightSize` em px
 *   (mesma unidade da sidebar esquerda), visualmente recolhido quando
 *   `showRight=false`. O conteúdo permanece montado para preservar estado;
 *   quando oculto, layout = 100% esquerda; sem painel
 *   residual sobrando 20px e estragando a janela.
 * - `rightCollapsed`: trata o painel direito como a sidebar esquerda
 *   colapsada — mantém uma faixa estreita de largura fixa com borda
 *   divisória, sem handle de resize (nada para arrastar).
 * - Handle: 4px de largura, cursor `col-resize`, arrasta para ajustar
 *   `rightSize` (px) entre `minRight` e `maxRight`.
 * - Imune a SSR: o `right` só é montado após o cliente decidir mostrá-lo;
 *   o tree estável (sempre 1 ou 3 filhos) não precisa de chave de remount.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { motion, useReducedMotion } from "motion/react";
import { WORKBENCH_WIDTH_TRANSITION } from "@/lib/layout/workbench-geometry";

interface HorizontalSplitProps {
  left: ReactNode;
  right: ReactNode | null;
  showRight: boolean;
  /** Largura do painel direito em px. Atualizada durante o drag. */
  rightSize: number;
  onResize: (size: number) => void;
  /** Largura mínima do painel direito em px. */
  minRight?: number;
  /** Largura máxima do painel direito em px. */
  maxRight?: number;
  /** Largura mínima do painel esquerdo (flex-1) em px — sem isso (`min-w-0`
   *  puro), arrastar o workbench bem largo numa janela estreita podia
   *  encolher o Header (esquerda) até sumir ícones inteiros (ajuda,
   *  configurações, mode-switch) em vez de só truncar texto. */
  minLeft?: number;
  className?: string;
  /** Quando true, `right` vira uma faixa estreita de largura fixa (sem resize). */
  rightCollapsed?: boolean;
  /** Largura em px da faixa colapsada. Default: 48 (w-12). */
  collapsedWidth?: number;
  /** Lado do painel fixo (`right`). Default "right" (atrás do flex-1). */
  side?: "left" | "right";
}

export function HorizontalSplit({
  left,
  right,
  showRight,
  rightSize,
  onResize,
  minRight = 180,
  maxRight = 720,
  className,
  rightCollapsed = false,
  collapsedWidth = 48,
  side = "right",
  minLeft = 360,
}: HorizontalSplitProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  const [isDragging, setIsDragging] = useState(false);
  const reducedMotion = useReducedMotion();

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    draggingRef.current = true;
    setIsDragging(true);
    (e.target as Element).setPointerCapture?.(e.pointerId);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingRef.current) return;
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const dist =
        side === "left" ? e.clientX - rect.left : rect.right - e.clientX;
      const clamped = Math.max(minRight, Math.min(maxRight, dist));
      onResize(clamped);
    },
    [maxRight, minRight, onResize, side],
  );

  const onPointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    setIsDragging(false);
    (e.target as Element).releasePointerCapture?.(e.pointerId);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }, []);

  // Garante restauração do cursor mesmo se o componente desmontar mid-drag.
  useEffect(() => {
    return () => {
      if (draggingRef.current) {
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      }
    };
  }, []);

  const rightWidth = Math.max(minRight, Math.min(maxRight, rightSize));

  // Calcula a largura alvo: 0 quando fechado, collapsedWidth ou rightWidth quando aberto.
  // Durante drag, `transition={{ duration: 0 }}` garante resposta imediata sem lag de spring.
  const targetWidth = showRight
    ? rightCollapsed
      ? collapsedWidth
      : rightWidth
    : 0;
  // Handle de resize (w-1 = 4px) só existe quando o painel direito está
  // aberto e não-colapsado — soma ao espaço reservado pro cálculo de
  // minWidth do flexPanel abaixo, senão sobra 4px de estouro mesmo com o
  // min() aplicado.
  const handleWidth = showRight && !rightCollapsed ? 4 : 0;
  const springTransition = reducedMotion
    ? { duration: 0 }
    : isDragging
      ? { duration: 0 }
      : WORKBENCH_WIDTH_TRANSITION;

  const handle = (
    <div
      role="separator"
      aria-orientation="vertical"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      className="w-1 bg-border/40 hover:bg-border transition-colors cursor-col-resize shrink-0"
    />
  );

  // overflow-visible: dropdowns do appbar (Header) não podem ser recortados
  // por este container — o conteúdo rolável já tem overflow-hidden interno.
  //
  // minWidth via min(): `minLeft` sozinho (número fixo em px) nunca
  // considerava o espaço que o painel direito (`targetWidth`) já ocupa —
  // num viewport mais estreito que `minLeft + targetWidth` (todo celular,
  // já que a faixa colapsada de 48px soma a esse mínimo de 360px = 408px),
  // o flexPanel estourava por cima do painel direito em vez de encolher
  // (achado real: bug de layout recorrente — reproduzido ao vivo em
  // 375px, painel de chat com min-width 360px sobrepondo os ícones do
  // workbench colapsado). `min(minLeft, 100% - targetWidth)` preserva o
  // piso de proteção original (Header não desaparece com o workbench
  // arrastado bem largo em telas largas) sem nunca exceder o espaço real
  // disponível.
  const flexPanel = (
    <div
      className="flex-1 overflow-visible"
      style={{
        minWidth: `min(${minLeft}px, calc(100% - ${targetWidth + handleWidth}px))`,
      }}
    >
      {left}
    </div>
  );

  // Painel animado: spring suave ao abrir/fechar, sem lag durante drag.
  const animatedPanel = (
    <motion.div
      animate={{ width: targetWidth }}
      initial={false}
      transition={springTransition}
      className={`shrink-0 overflow-hidden ${
        rightCollapsed && showRight
          ? `border-border/60 ${side === "left" ? "border-r" : "border-l"}`
          : ""
      }`}
    >
      {right}
    </motion.div>
  );

  return (
    <div ref={containerRef} className={`flex h-full ${className ?? ""}`}>
      {side === "left" && (
        <>
          {animatedPanel}
          {showRight && !rightCollapsed && handle}
        </>
      )}
      {flexPanel}
      {side === "right" && (
        <>
          {showRight && !rightCollapsed && handle}
          {animatedPanel}
        </>
      )}
    </div>
  );
}
