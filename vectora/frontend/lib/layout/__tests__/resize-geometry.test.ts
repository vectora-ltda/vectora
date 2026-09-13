import { describe, expect, it } from "vitest";
import { getPanelWidthFromPointer, getResizeDelta } from "../resize-geometry";

describe("resize geometry", () => {
  const rect = { left: 100, right: 500 };

  it.each([
    ["left", 300, 200],
    ["right", 300, 200],
  ] as const)(
    "calcula largura física para coluna %s",
    (side, pointer, expected) => {
      expect(getPanelWidthFromPointer(pointer, rect, side)).toBe(expected);
    },
  );

  it("inverte o delta do teclado conforme o lado físico", () => {
    expect(getResizeDelta("ArrowRight", "left")).toBe(16);
    expect(getResizeDelta("ArrowLeft", "left")).toBe(-16);
    expect(getResizeDelta("ArrowLeft", "right")).toBe(16);
    expect(getResizeDelta("ArrowRight", "right")).toBe(-16);
  });
});
