import type {
  ResolvedSurfaceMode,
  WorkbenchSettingsSurfaceMode,
} from "./types/workbench-settings";

/**
 * Resolves a declared browser surface against the runtime bridge.
 * Native Chromium views cannot exist in a regular web session.
 */
export function resolveBrowserSurfaceMode(
  declared: WorkbenchSettingsSurfaceMode,
  hasDesktopBridge: boolean,
): ResolvedSurfaceMode {
  if (declared !== "native-view") return declared;
  return hasDesktopBridge ? "native-view" : "unavailable";
}
