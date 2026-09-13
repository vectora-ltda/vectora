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

/** Breakpoint `md` do Tailwind (768px) — abaixo dele, layouts multi-painel
 * colapsam para um único painel visível por vez. */
export const MD_BREAKPOINT_QUERY = "(max-width: 767px)";

export function useIsNarrowViewport(): boolean {
  return useMediaQuery(MD_BREAKPOINT_QUERY);
}

/**
 * Returns the unscaled window width used for responsive layout decisions.
 *
 * Electron's appearance scale is implemented with `webContents.setZoomLevel`.
 * That changes CSS pixels and therefore `innerWidth`/`matchMedia`, even though
 * the physical window is unchanged. `outerWidth` remains the window geometry,
 * so the UI scale can never accidentally switch the IDE into mobile layout.
 */
export function getUnscaledViewportWidth(): number {
  if (typeof window === "undefined") return Number.POSITIVE_INFINITY;
  const isElectron = Boolean(window.vectora?.windowControls);
  if (isElectron && window.outerWidth > 0) return window.outerWidth;
  return window.visualViewport?.width ?? window.innerWidth;
}

/** Tailwind `sm` breakpoint: below 640px is the smartphone layout. */
export const IDE_MOBILE_BREAKPOINT = 640;

export type IdeLayoutState = "wide" | "mobile";

/** Classifies the IDE from the unscaled window geometry. */
export function getIdeLayoutState(width: number): IdeLayoutState {
  if (width < IDE_MOBILE_BREAKPOINT) return "mobile";
  return "wide";
}

export function useIdeLayoutState(): IdeLayoutState {
  // Keep the first render identical between SSR and the browser. The actual
  // unscaled window geometry is read after mount, so mobile can switch in
  // without producing a hydration mismatch.
  const [state, setState] = useState<IdeLayoutState>("wide");

  useEffect(() => {
    const update = () =>
      setState(getIdeLayoutState(getUnscaledViewportWidth()));
    // oxlint-disable-next-line react/set-state-in-effect
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  return state;
}

/** Whether the IDE should use its smartphone-only mobile layout. */
export function useIsNarrowIdeViewport(): boolean {
  return useIdeLayoutState() === "mobile";
}
