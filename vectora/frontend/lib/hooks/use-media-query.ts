import { useEffect, useState } from "react";

/**
 * Reage a uma media query via `matchMedia`, com valor inicial síncrono
 * (evita flash — o listener só cobre mudanças pós-mount, ex: resize da
 * janela ou rotação do dispositivo).
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    () => typeof window !== "undefined" && window.matchMedia(query).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(query);
    // Sincroniza com o sistema externo matchMedia — o valor pode ter mudado
    // entre o cálculo síncrono do useState e o efeito rodar.
    // oxlint-disable-next-line react/set-state-in-effect
    setMatches(mql.matches);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, [query]);

  return matches;
}

/**
 * IDE multi-panel minimum width, including the persistent session sidebar and
 * the navigation, editor, and chat columns. Below 988px, show one panel at a
 * time so the workbench is not clipped by the session page overflow boundary.
 */
export const MD_BREAKPOINT_QUERY = "(max-width: 987px)";

export function useIsNarrowViewport(): boolean {
  return useMediaQuery(MD_BREAKPOINT_QUERY);
}
