import { describe, expect, it } from "vitest";
import {
  getMaxPanelWidth,
  getResponsivePanelWidths,
} from "../responsive-panel-width";

describe("getMaxPanelWidth", () => {
  it.each([
    [639, 260],
    [640, 260],
    [767, 260],
    [768, 320],
    [1023, 320],
    [1024, 400],
    [1279, 400],
    [1280, 480],
    [1920, 480],
  ])("limita %s px a %s px", (viewportWidth, expected) => {
    expect(getMaxPanelWidth(viewportWidth)).toBe(expected);
  });

  it("reduz os três painéis em viewport média e restaura a preferência no desktop", () => {
    const preferred = {
      sidebarWidth: 480,
      chatSidebarWidth: 480,
      splitSize: 480,
    };
    expect(getResponsivePanelWidths(preferred, 900)).toEqual({
      sidebarWidth: 320,
      chatSidebarWidth: 320,
      splitSize: 320,
    });
    expect(getResponsivePanelWidths(preferred, 1100)).toEqual({
      sidebarWidth: 400,
      chatSidebarWidth: 400,
      splitSize: 400,
    });
    expect(getResponsivePanelWidths(preferred, 1444)).toEqual(preferred);
  });
});
