"use client";

/**
 * Menu de clique direito minimalista para o painel Git (arquivos e commits).
 *
 * O Vectora não traz `@radix-ui/react-context-menu`; este componente leve
 * posiciona um menu flutuante no cursor e fecha em clique fora / Escape /
 * scroll. Uso:
 *
 *   const menu = useContextMenu();
 *   <div onContextMenu={(e) => menu.open(e, items)}>…</div>
 *   {menu.element}
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface ContextMenuItem {
  label: string;
  onSelect: () => void;
  danger?: boolean;
}

interface MenuState {
  x: number;
  y: number;
  items: ContextMenuItem[];
}

export function useContextMenu() {
  const [state, setState] = useState<MenuState | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  const open = useCallback((e: React.MouseEvent, items: ContextMenuItem[]) => {
    if (items.length === 0) return;
    e.preventDefault();
    setState({ x: e.clientX, y: e.clientY, items });
  }, []);

  const close = useCallback(() => setState(null), []);

  useEffect(() => {
    if (!state) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("mousedown", onDocClick);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("mousedown", onDocClick);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [state, close]);

  const element =
    state && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={ref}
            role="menu"
            className="fixed z-50 min-w-[180px] rounded-md border border-border/60 bg-popover py-1 shadow-md text-xs"
            style={{
              top: Math.min(
                state.y,
                window.innerHeight - 8 - state.items.length * 28,
              ),
              left: Math.min(state.x, window.innerWidth - 188),
            }}
          >
            {state.items.map((item, i) => (
              <button
                key={i}
                role="menuitem"
                onClick={() => {
                  item.onSelect();
                  close();
                }}
                className={`flex w-full items-center px-3 py-1.5 text-left hover:bg-muted/60 ${
                  item.danger ? "text-destructive" : "text-foreground"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>,
          document.body,
        )
      : null;

  return { open, close, element };
}
