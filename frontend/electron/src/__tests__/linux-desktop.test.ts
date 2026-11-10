import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  DEVELOPMENT_DESKTOP_NAME,
  registerDevelopmentDesktop,
} from "../linux-desktop";

const directories: string[] = [];
afterEach(() =>
  directories
    .splice(0)
    .forEach((dir) => rmSync(dir, { recursive: true, force: true })),
);

describe("Linux development desktop identity", () => {
  it("registers a matching identity and absolute icon with safely quoted checkout paths", () => {
    const dataHome = mkdtempSync(join(tmpdir(), "vectora-desktop-"));
    directories.push(dataHome);
    const options = {
      dataHome,
      executable: "/tools/Electron App/electron",
      main: "/repo 100%/main.js",
      icon: "/repo/icon.png",
    };
    registerDevelopmentDesktop(options);
    registerDevelopmentDesktop(options);
    const entry = readFileSync(
      join(dataHome, "applications", DEVELOPMENT_DESKTOP_NAME),
      "utf8",
    );
    expect(entry).toContain(
      'Exec="/tools/Electron App/electron" "/repo 100%%/main.js"',
    );
    expect(entry).toContain("Icon=/repo/icon.png");
    expect(entry).toContain("StartupWMClass=company.vectora.desktop.dev\n");
  });

  it("rejects path injection instead of adding another desktop key", () => {
    const dataHome = mkdtempSync(join(tmpdir(), "vectora-desktop-"));
    directories.push(dataHome);
    expect(() =>
      registerDevelopmentDesktop({
        dataHome,
        executable: "/electron",
        main: "/main.js",
        icon: "/icon.png\nExec=malicious",
      }),
    ).toThrow();
  });
});
