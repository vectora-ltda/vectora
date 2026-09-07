import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { describe, expect, it } from "vitest";
import { filesUnder, isIgnoredPath, lintSource, runCli } from "./lint-i18n";

const execFileAsync = promisify(execFile);

describe("lint-i18n", () => {
  it("reports visible JSX text and text attributes", () => {
    const violations = lintSource(
      '<button aria-label="Salvar">Salvar</button>',
      "fixture.tsx",
    );
    expect(violations).toHaveLength(2);
    expect(violations.map(({ message }) => message)).toEqual([
      expect.stringContaining("aria-label"),
      expect.stringContaining("Visible JSX text"),
    ]);
  });

  it("catches literal strings inside JSX expressions", () => {
    expect(
      lintSource('<button title={"Salvar"}>Continuar</button>', "fixture.tsx"),
    ).toHaveLength(2);
  });

  it("detects a static JSX expression child", () => {
    expect(
      lintSource('<button>{"Salvar"}</button>', "fixture.tsx"),
    ).toHaveLength(1);
  });

  it("detects static template literals", () => {
    expect(
      lintSource("<button>{`Salvar`}</button>", "fixture.tsx"),
    ).toHaveLength(1);
    expect(lintSource("<img alt={`Logotipo`} />", "fixture.tsx")).toHaveLength(
      1,
    );
  });

  it("returns a failing exit code only when files violate the rule", async () => {
    const directory = await mkdtemp(join(tmpdir(), "lint-i18n-"));
    try {
      const valid = join(directory, "valid.tsx");
      const invalid = join(directory, "invalid.tsx");
      await writeFile(valid, "<button>{m.save()}</button>");
      await writeFile(invalid, "<button>Salvar</button>");
      expect(await runCli([valid])).toBe(0);
      expect(await runCli([invalid])).toBe(1);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("reports exit status and diagnostics through the package command", async () => {
    const directory = await mkdtemp(join(tmpdir(), "lint-i18n-command-"));
    try {
      const valid = join(directory, "valid.tsx");
      const invalid = join(directory, "invalid.tsx");
      await writeFile(valid, "<button>{m.save()}</button>");
      await writeFile(invalid, "<button>Salvar</button>");

      const command = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
      const result = await execFileAsync(command, ["lint:i18n", "--", valid], {
        cwd: process.cwd(),
        shell: true,
      });
      expect(result.stdout).toBe("");

      await expect(
        execFileAsync(command, ["lint:i18n", "--", invalid], {
          cwd: process.cwd(),
          shell: true,
        }),
      ).rejects.toMatchObject({
        code: 1,
        stderr: expect.stringContaining("invalid.tsx:1:9"),
      });
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("applies exclusions to every path mode", () => {
    expect(isIgnoredPath("components/__tests__/fixture.tsx")).toBe(true);
    expect(isIgnoredPath("components\\__tests__\\fixture.tsx")).toBe(true);
    expect(isIgnoredPath("components/e2e/fixture.tsx")).toBe(true);
    expect(isIgnoredPath("lib/paraglide/messages.ts")).toBe(true);
    expect(isIgnoredPath("lib\\paraglide\\messages.ts")).toBe(true);
    expect(isIgnoredPath("node_modules/pkg/index.tsx")).toBe(true);
    expect(isIgnoredPath("scripts/lint-i18n.test.ts")).toBe(true);
    expect(isIgnoredPath("src/Button.spec.tsx")).toBe(true);
    expect(isIgnoredPath("src\\Button.spec.tsx")).toBe(true);
    expect(isIgnoredPath("generated/messages.ts")).toBe(true);
    expect(isIgnoredPath("components/settings/panel.tsx")).toBe(false);
  });

  it("filters test files during a full directory scan", async () => {
    const directory = await mkdtemp(join(tmpdir(), "lint-i18n-scan-"));
    try {
      await mkdir(join(directory, "src"));
      await writeFile(join(directory, "src", "Button.test.tsx"), "");
      await writeFile(join(directory, "src", "Button.spec.tsx"), "");
      await writeFile(join(directory, "src", "Button.tsx"), "");
      await expect(filesUnder(directory)).resolves.toEqual([
        join(directory, "src", "Button.tsx"),
      ]);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("accepts message calls and ignores technical attributes", () => {
    const violations = lintSource(
      'const label = m.common_save(); const dynamic = mDyn("common.save");\n' +
        '<button className="SaveButton" data-testid="save" title={label}>{label}</button>',
      "fixture.tsx",
    );
    expect(violations).toEqual([]);
  });
});
