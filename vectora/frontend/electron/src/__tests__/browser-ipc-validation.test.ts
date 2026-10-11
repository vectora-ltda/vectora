import { describe, expect, it } from "vitest";
import {
  isValidBrowserUrl,
  isValidOriginPermissions,
  isValidBrowserViewKind,
  isValidProfileId,
  isValidViewBounds,
  isValidViewId,
} from "../browser-ipc-validation.js";

describe("browser IPC validation", () => {
  it("accepts only canonical HTTP origins and explicit permission modes", () => {
    expect(
      isValidOriginPermissions({
        "https://example.com": "allow",
        "http://localhost:3000": "deny",
      }),
    ).toBe(true);
    for (const value of [
      null,
      [],
      { "https://example.com/path": "allow" },
      { "file:///": "allow" },
      { "https://example.com": "other" },
      { "https://user:pass@example.com": "allow" },
    ]) {
      expect(isValidOriginPermissions(value)).toBe(false);
    }
  });
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
    expect(isValidBrowserUrl("https://example.com/path")).toBe(true);
    expect(isValidBrowserUrl("chrome://settings")).toBe(false);
    expect(isValidBrowserUrl("chrome://settings", "native-settings")).toBe(
      true,
    );
    expect(
      isValidBrowserUrl("chrome://settings/passwords/", "native-settings"),
    ).toBe(true);
    expect(
      isValidBrowserUrl(
        "  CHROME://SETTINGS/Passwords///  ",
        "native-settings",
      ),
    ).toBe(true);
    expect(
      isValidBrowserUrl("chrome://settings/flags", "native-settings"),
    ).toBe(false);
    expect(isValidBrowserUrl("chrome://settings/help", "native-settings")).toBe(
      false,
    );
    expect(isValidBrowserUrl("chrome://flags", "native-settings")).toBe(false);
    expect(isValidBrowserUrl("file:///tmp/example")).toBe(false);
    expect(isValidBrowserUrl("not a URL")).toBe(false);
    expect(isValidBrowserUrl("")).toBe(false);
  });
});
