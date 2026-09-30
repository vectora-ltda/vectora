import { useSettingsStore } from "@/lib/stores/settings-store";
import { useIsDark } from "@/lib/hooks/use-is-dark";
import { resolveMonacoTheme } from "./godot-theme";

export function useMonacoTheme(language?: string): string {
  const presetId = useSettingsStore((state) => state.themePreset);
  const isDark = useIsDark();
  return resolveMonacoTheme({ presetId, isDark, language });
}
