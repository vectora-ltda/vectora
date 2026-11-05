export type PhysicalSide = "left" | "right";

export function getPanelWidthFromPointer(
  pointerX: number,
  rect: Pick<DOMRect, "left" | "right">,
  side: PhysicalSide,
): number {
  return side === "left" ? pointerX - rect.left : rect.right - pointerX;
}

export function getResizeDelta(
  key: "ArrowLeft" | "ArrowRight",
  side: PhysicalSide,
  step = 16,
): number {
  const increases =
    side === "left" ? key === "ArrowRight" : key === "ArrowLeft";
  return increases ? step : -step;
}
