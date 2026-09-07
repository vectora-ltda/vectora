import { describe, expect, it } from "vitest";
import { isIgnoredPath, lintSource } from "./lint-i18n";

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

  it("applies exclusions to every path mode", () => {
    expect(isIgnoredPath("components/__tests__/fixture.tsx")).toBe(true);
    expect(isIgnoredPath("components\\__tests__\\fixture.tsx")).toBe(true);
    expect(isIgnoredPath("components/e2e/fixture.tsx")).toBe(true);
    expect(isIgnoredPath("lib/paraglide/messages.ts")).toBe(true);
    expect(isIgnoredPath("lib\\paraglide\\messages.ts")).toBe(true);
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
