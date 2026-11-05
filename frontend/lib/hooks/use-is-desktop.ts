import { useEffect, useState } from "react";

/**
 * True quando rodando dentro do app desktop (Electron, `window.vectora`
 * exposto pelo preload) — false em qualquer acesso via browser puro.
 * Lido só depois do mount (useEffect) porque `window.vectora` não existe
 * durante SSR/hidratação.
 */
export function useIsDesktop(): boolean {
  const [desktop, setDesktop] = useState(false);

  useEffect(() => {
    // Sincroniza com o sistema externo window.vectora (só existe no client,
    // após o preload do Electron) — não é derivável durante o render.
    // oxlint-disable-next-line react/set-state-in-effect
    setDesktop(
      typeof window !== "undefined" && Boolean(window.vectora?.windowControls),
    );
  }, []);

  return desktop;
}
