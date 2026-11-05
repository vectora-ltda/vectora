"use client";

/**
 * useDelayedLoading — atrasa a exibição de um skeleton/spinner por `delayMs`
 * (default 100ms) para evitar o "flash" em buscas que resolvem quase
 * instantaneamente (cache quente, rede local).
 *
 * Sem isso, todo fetch — mesmo um que leva 20ms — pisca um skeleton inteiro
 * na tela e o usuário percebe como "tremedeira" em vez de fluidez.
 *
 * @example
 *   const showSkeleton = useDelayedLoading(status === "loading" && !hasLoaded);
 *   return showSkeleton ? <ThreadListSkeleton /> : <ThreadList items={items} />;
 */

import { useEffect, useRef, useState } from "react";

const DEFAULT_DELAY_MS = 100;

export function useDelayedLoading(
  isLoading: boolean,
  delayMs: number = DEFAULT_DELAY_MS,
): boolean {
  const [shouldShow, setShouldShow] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (isLoading) {
      timerRef.current = setTimeout(() => setShouldShow(true), delayMs);
    } else {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = null;
      // Sincroniza com o timer (sistema externo), não deriva de props puras.
      // oxlint-disable-next-line react/set-state-in-effect
      setShouldShow(false);
    }
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [isLoading, delayMs]);

  return isLoading && shouldShow;
}
