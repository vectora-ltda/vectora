import { beforeEach, describe, expect, it } from "vitest";
import { getBrowserProfileSettings, useSettingsStore } from "../settings-store";

beforeEach(() => {
  useSettingsStore.getState().resetSettings();
});

describe("settings-store — Browser profile isolation", () => {
  it("keeps zoom, popup and origin policies separate per profile", () => {
    const settings = useSettingsStore.getState();
    settings.setBrowserProfileSettings("profile-a", {
      allowPopups: true,
      zoomPercent: 125,
      permissionMode: "allow",
      originPermissions: { "https://a.example": "allow" },
    });

    expect(
      getBrowserProfileSettings(useSettingsStore.getState(), "profile-a"),
    ).toMatchObject({
      allowPopups: true,
      zoomPercent: 125,
      permissionMode: "allow",
      originPermissions: { "https://a.example": "allow" },
    });
    expect(
      getBrowserProfileSettings(useSettingsStore.getState(), "profile-b"),
    ).toMatchObject({
      allowPopups: false,
      zoomPercent: 100,
      permissionMode: "deny",
      originPermissions: {},
    });
  });

  it("persists the profile map and can remove one profile without affecting another", () => {
    const settings = useSettingsStore.getState();
    settings.setBrowserProfileSettings("profile-a", { zoomPercent: 140 });
    settings.setBrowserProfileSettings("profile-b", { zoomPercent: 80 });
    settings.resetBrowserProfileSettings("profile-a");

    expect(
      useSettingsStore.getState().browserProfileSettings,
    ).not.toHaveProperty("profile-a");
    expect(
      useSettingsStore.getState().browserProfileSettings["profile-b"]
        ?.zoomPercent,
    ).toBe(80);
    const partialize = useSettingsStore.persist.getOptions().partialize;
    expect(partialize!(useSettingsStore.getState())).toMatchObject({
      browserProfileSettings: {
        "profile-b": expect.objectContaining({ zoomPercent: 80 }),
      },
    });
  });

  it("preserves origin policies when a profile update omits them", () => {
    const settings = useSettingsStore.getState();
    settings.setBrowserProfileSettings("profile-a", {
      originPermissions: { "https://a.example": "allow" },
    });
    settings.setBrowserProfileSettings("profile-a", { zoomPercent: 125 });

    expect(
      getBrowserProfileSettings(useSettingsStore.getState(), "profile-a")
        .originPermissions,
    ).toEqual({ "https://a.example": "allow" });
  });
});
