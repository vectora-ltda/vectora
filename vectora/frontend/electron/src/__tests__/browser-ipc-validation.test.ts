import { describe, expect, it } from "vitest";
import {
  isValidBrowserUrl,
  isValidBrowserViewKind,
  isValidProfileId,
  isValidViewBounds,
  isValidViewId,
} from "../browser-ipc-validation.js";

describe("browser IPC validation", () => {
  it("accepts only bounded profile identifiers", () => {
    expect(isValidProfileId("default")).toBe(true);
    expect(isValidProfileId("session-d29ya3Nlcg")).toBe(true);
    expect(isValidProfileId("persist:browser-x")).toBe(false);
    expect(isValidProfileId("session/unsafe")).toBe(false);
  });

  it("validates kinds, ids, bounds and URLs", () => {
    expect(isValidBrowserViewKind("tab")).toBe(true);
    expect(isValidBrowserViewKind("native-settings")).toBe(true);
    expect(isValidBrowserViewKind("settings")).toBe(false);
    expect(isValidViewId(1)).toBe(true);
    expect(isValidViewId(1.2)).toBe(false);
    expect(isValidViewBounds({ x: 0, y: 0, width: 10, height: 10 })).toBe(true);
    expect(isValidViewBounds({ x: -1, y: 0, width: 10, height: 10 })).toBe(
      false,
    );
    expect(isValidBrowserUrl("chrome://settings")).toBe(true);
    expect(isValidBrowserUrl("")).toBe(false);
  });
});
