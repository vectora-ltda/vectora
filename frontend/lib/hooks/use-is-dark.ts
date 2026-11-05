import { useEffect, useState } from "react";
import { useSettingsStore } from "@/lib/stores/settings-store";

/**
 * Resolve se o tema ativo é escuro, lendo do settings-store (não next-themes).
 * Suporta "light" / "dark" / "system" — no modo "system" reage ao media query.
 */
export function useIsDark(): boolean {
  const theme = useSettingsStore((s) => s.theme);
  const [sysDark, setSysDark] = useState(() =>
    typeof window === "undefined" || typeof window.matchMedia !== "function"
      ? true
      : window.matchMedia("(prefers-color-scheme: dark)").matches,
  );

  useEffect(() => {
    if (theme !== "system") return;
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    // This synchronizes an external media-query snapshot on mode transition.
    // oxlint-disable-next-line react/set-state-in-effect
    setSysDark(mq.matches);
    // The listener is also an external subscription and must update React state.
    // oxlint-disable-next-line react/set-state-in-effect
    const handler = (e: MediaQueryListEvent) => setSysDark(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [theme]);

  if (theme === "light") return false;
  if (theme === "dark") return true;
  return sysDark;
}
