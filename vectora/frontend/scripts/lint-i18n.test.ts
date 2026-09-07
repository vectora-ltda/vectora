import { describe, expect, it } from "vitest";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { isIgnoredPath, lintSource, runCli } from "./lint-i18n";

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

  it("applies exclusions to every path mode", () => {
    expect(isIgnoredPath("components/__tests__/fixture.tsx")).toBe(true);
    expect(isIgnoredPath("components\\__tests__\\fixture.tsx")).toBe(true);
    expect(isIgnoredPath("components/e2e/fixture.tsx")).toBe(true);
    expect(isIgnoredPath("lib/paraglide/messages.ts")).toBe(true);
    expect(isIgnoredPath("lib\\paraglide\\messages.ts")).toBe(true);
    expect(isIgnoredPath("node_modules/pkg/index.tsx")).toBe(true);
    expect(isIgnoredPath("generated/messages.ts")).toBe(true);
    expect(isIgnoredPath("components/settings/panel.tsx")).toBe(false);
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
