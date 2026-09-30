import { describe, expect, it } from "vitest";
import {
  isNativeSettingsRoute,
  resolveBrowserSurfaceMode,
} from "../browser-capabilities";

describe("browser capabilities", () => {
  it("marks native settings unavailable without the desktop bridge", () => {
    expect(resolveBrowserSurfaceMode("form")).toBe("form");
    expect(resolveBrowserSurfaceMode("link")).toBe("link");
    expect(resolveBrowserSurfaceMode("native-view")).toBe("unavailable");
  });

  it("accepts only explicitly supported Chromium settings routes", () => {
    expect(isNativeSettingsRoute("chrome://settings")).toBe(true);
    expect(isNativeSettingsRoute("chrome://settings/privacy")).toBe(true);
    expect(isNativeSettingsRoute(" CHROME://SETTINGS/PRIVACY ")).toBe(true);
    expect(isNativeSettingsRoute(" chrome://settings/unknown ")).toBe(false);
    expect(isNativeSettingsRoute("https://example.com")).toBe(false);
  });
});
