"use client";

/**
 * ThemeProvider
 *
 * Envolve o app com o ThemeProvider do next-themes.
 * ThemeSync sincroniza o tema persistido no settings-store com next-themes
 * na montagem e sempre que o usuário alterar via PreferenciasTab.
 */

import { useEffect } from "react";
import { ThemeProvider as NextThemesProvider, useTheme } from "next-themes";
import { useSettingsStore } from "@/lib/stores/settings-store";

interface ThemeProviderProps {
  children: React.ReactNode;
}

/**
 * Sincroniza o tema do Zustand settings-store → next-themes.
 * Deve renderizar dentro do NextThemesProvider para ter acesso ao contexto.
 */
function ThemeSync() {
  const { setTheme } = useTheme();
  const theme = useSettingsStore((s) => s.theme);

  useEffect(() => {
    setTheme(theme);
  }, [theme, setTheme]);

  return null;
}

export function ThemeProvider({ children }: ThemeProviderProps) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      <ThemeSync />
      {children}
    </NextThemesProvider>
  );
}
