import type {
  ResolvedSurfaceMode,
  WorkbenchSettingsSurfaceMode,
} from "./types/workbench-settings";

const NATIVE_SETTINGS_PATHS = new Set([
  "",
  "/",
  "/autofill",
  "/clearbrowserdata",
  "/content",
  "/downloads",
  "/extensions",
  "/languages",
  "/onstartup",
  "/passwords",
  "/performance",
  "/privacy",
  "/reset",
  "/search",
  "/security",
  "/sitedata",
  "/syncsetup",
  "/system",
  "/youandgoogle",
]);

/** Native Chromium settings are available only through the Electron bridge. */
export function resolveBrowserSurfaceMode(
  declared: WorkbenchSettingsSurfaceMode,
): ResolvedSurfaceMode {
  if (
    declared === "native-view" &&
    (typeof window === "undefined" || !window.vectora?.browserView)
  ) {
    return "unavailable";
  }
  return declared;
}

/** Routes accepted by the native settings view, mirrored from the main process. */
export function isNativeSettingsRoute(raw: string): boolean {
  try {
    const url = new URL(raw.trim());
    if (url.protocol !== "chrome:" || url.hostname.toLowerCase() !== "settings")
      return false;
    if (url.search || url.hash) return false;
    return NATIVE_SETTINGS_PATHS.has(url.pathname.trim().toLowerCase());
  } catch {
    return false;
  }
}
