import { SIDE_COLUMN_MIN_WIDTH } from "@/lib/layout/panel-geometry";

/** Tailwind-aligned viewport breakpoints used by resizable side panels. */
export const RESPONSIVE_PANEL_BREAKPOINTS = {
  sm: 640,
  md: 768,
  lg: 1024,
  xl: 1280,
} as const;

/** Maximum width shared by side panels at each responsive viewport tier. */
export function getMaxPanelWidth(viewportWidth: number): number {
  if (viewportWidth < RESPONSIVE_PANEL_BREAKPOINTS.md) return 260;
  if (viewportWidth < RESPONSIVE_PANEL_BREAKPOINTS.lg) return 320;
  if (viewportWidth < RESPONSIVE_PANEL_BREAKPOINTS.xl) return 400;
  return 480;
}

export interface PanelWidths {
  sidebarWidth: number;
  chatSidebarWidth: number;
  splitSize: number;
}

/** Applies the responsive ceiling while preserving the desktop preferences. */
export function getResponsivePanelWidths(
  preferred: PanelWidths,
  viewportWidth: number,
): PanelWidths {
  const maxWidth = Math.min(
    viewportWidth - 320,
    getMaxPanelWidth(viewportWidth),
  );
  if (maxWidth <= 0 || viewportWidth >= RESPONSIVE_PANEL_BREAKPOINTS.xl) {
    return preferred;
  }
  return {
    sidebarWidth: Math.max(
      SIDE_COLUMN_MIN_WIDTH,
      Math.min(preferred.sidebarWidth, maxWidth),
    ),
    chatSidebarWidth: Math.max(
      SIDE_COLUMN_MIN_WIDTH,
      Math.min(preferred.chatSidebarWidth, maxWidth),
    ),
    splitSize: Math.max(220, Math.min(preferred.splitSize, maxWidth)),
  };
}
