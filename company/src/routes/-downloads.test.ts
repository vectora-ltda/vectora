import { describe, it, expect, afterEach } from "vitest";
import { detectOS, downloadHref, PLATFORMS } from "./downloads";
import { REQUIRED_RELEASE_FORMATS } from "../../../services/src/updates/release-matrix";

// detectOS mapeia o userAgent → plataforma para destacar o download recomendado.
function setUA(ua: string): void {
  Object.defineProperty(navigator, "userAgent", {
    value: ua,
    configurable: true,
  });
}

const original = navigator.userAgent;
afterEach(() => setUA(original));

describe("detectOS", () => {
  it("Windows", () => {
    setUA("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36");
    expect(detectOS()).toBe("windows");
  });

  it("macOS", () => {
    setUA("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605");
    expect(detectOS()).toBe("macos");
  });

  it("Linux", () => {
    setUA("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36");
    expect(detectOS()).toBe("linux");
  });

  it("userAgent desconhecido → null (edge)", () => {
    setUA("CoolBot/1.0 (+https://example.com/bot)");
    expect(detectOS()).toBeNull();
  });
});

describe("download matrix", () => {
  it("exposes both Windows installers for x64 and ARM64", () => {
    const windows = PLATFORMS.find((platform) => platform.os === "windows");
    expect(windows).toBeDefined();
    const targets = [windows!.primary, ...windows!.others].map(
      ({ arch, ext }) => `${arch}.${ext}`,
    );
    expect(targets).toEqual(
      expect.arrayContaining(["x64.exe", "x64.msi", "arm64.exe", "arm64.msi"]),
    );
  });

  it("exposes DMG and ZIP for both macOS architectures", () => {
    const mac = PLATFORMS.find((platform) => platform.os === "macos");
    expect(mac).toBeDefined();
    const targets = [mac!.primary, ...mac!.others].map(
      ({ arch, ext }) => `${arch}.${ext}`,
    );
    expect(targets).toEqual(
      expect.arrayContaining(["x64.dmg", "x64.zip", "arm64.dmg", "arm64.zip"]),
    );
  });

  it("exposes every Linux package format on x64 and ARM64", () => {
    const linux = PLATFORMS.find((platform) => platform.os === "linux");
    expect(linux).toBeDefined();
    const targets = new Set(
      [linux!.primary, ...linux!.others].map(
        ({ arch, ext }) => `${arch}.${ext}`,
      ),
    );
    for (const arch of ["x64", "arm64"]) {
      for (const ext of [
        "AppImage",
        "deb",
        "rpm",
        "snap",
        "pkg.tar.zst",
        "flatpak",
        "tar.gz",
      ]) {
        expect(targets).toContain(`${arch}.${ext}`);
      }
    }
  });

  it("creates a download URL using the same OS/architecture/format contract as Services", () => {
    expect(downloadHref("linux", "arm64", "pkg.tar.zst")).toBe(
      "https://services.vectora.company/download/latest/linux-arm64.pkg.tar.zst",
    );
  });

  it("does not render duplicate download targets", () => {
    for (const platform of PLATFORMS) {
      const targets = [platform.primary, ...platform.others].map(
        ({ arch, ext }) => `${platform.osToken}-${arch}.${ext}`,
      );
      expect(new Set(targets).size).toBe(targets.length);
    }
  });

  it("keeps the Company download links in sync with the R2 release matrix", () => {
    for (const [target, formats] of Object.entries(REQUIRED_RELEASE_FORMATS)) {
      const [osToken, arch] = target.split("/");
      const platform = PLATFORMS.find((item) => item.osToken === osToken);
      expect(platform, `missing platform card for ${target}`).toBeDefined();
      const visibleFormats = [platform!.primary, ...platform!.others]
        .filter((variant) => variant.arch === arch)
        .map((variant) => variant.ext)
        .sort();
      expect(visibleFormats).toEqual([...formats].sort());
    }
  });
});
