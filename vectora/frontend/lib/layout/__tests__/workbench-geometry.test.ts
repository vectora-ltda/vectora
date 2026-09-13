import { describe, expect, it } from "vitest";
import {
  WORKBENCH_RAIL_WIDTH,
  clampWorkbenchContentWidth,
  getWorkbenchGroupWidth,
} from "@/lib/layout/workbench-geometry";

describe("workbench geometry", () => {
  it("reserva somente a rail quando fechado", () => {
    expect(getWorkbenchGroupWidth(false, 360)).toBe(WORKBENCH_RAIL_WIDTH);
  });

  it("soma rail e conteúdo quando aberto e respeita limites", () => {
    expect(getWorkbenchGroupWidth(true, 360)).toBe(408);
    expect(clampWorkbenchContentWidth(100)).toBe(220);
    expect(clampWorkbenchContentWidth(900)).toBe(480);
  });
});
