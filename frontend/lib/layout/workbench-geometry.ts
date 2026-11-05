export const WORKBENCH_RAIL_WIDTH = 48;
export const WORKBENCH_CONTENT_MIN_WIDTH = 220;
export const WORKBENCH_CONTENT_MAX_WIDTH = 480;

export function clampWorkbenchContentWidth(width: number): number {
  return Math.min(
    WORKBENCH_CONTENT_MAX_WIDTH,
    Math.max(WORKBENCH_CONTENT_MIN_WIDTH, width),
  );
}

export function getWorkbenchGroupWidth(
  open: boolean,
  contentWidth: number,
): number {
  return open
    ? WORKBENCH_RAIL_WIDTH + clampWorkbenchContentWidth(contentWidth)
    : WORKBENCH_RAIL_WIDTH;
}

/** Width animation used by the legacy HorizontalSplit workbench. */
export const WORKBENCH_WIDTH_TRANSITION = {
  type: "spring" as const,
  stiffness: 260,
  damping: 26,
};
