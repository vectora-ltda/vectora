import { describe, expect, it } from "vitest";
import { resolveBrowserSurfaceMode } from "../browser-capabilities";

describe("resolveBrowserSurfaceMode", () => {
  it("keeps form and link surfaces available without Electron", () => {
    expect(resolveBrowserSurfaceMode("form", false)).toBe("form");
    expect(resolveBrowserSurfaceMode("link", false)).toBe("link");
  });

  it("marks native views unavailable without the desktop bridge", () => {
    expect(resolveBrowserSurfaceMode("native-view", false)).toBe("unavailable");
  });

  it("preserves native views when Electron is available", () => {
    expect(resolveBrowserSurfaceMode("native-view", true)).toBe("native-view");
  });
});
