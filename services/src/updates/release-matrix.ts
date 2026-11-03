/** Required public desktop packages by platform and architecture. */
export const REQUIRED_RELEASE_FORMATS: Record<string, readonly string[]> = {
  "win/x64": ["exe", "msi"],
  "win/arm64": ["exe", "msi"],
  "mac/x64": ["dmg", "zip"],
  "mac/arm64": ["dmg", "zip"],
  "linux/x64": [
    "AppImage",
    "deb",
    "rpm",
    "snap",
    "pkg.tar.zst",
    "flatpak",
    "tar.gz",
  ],
  "linux/arm64": [
    "AppImage",
    "deb",
    "rpm",
    "snap",
    "pkg.tar.zst",
    "flatpak",
    "tar.gz",
  ],
};
