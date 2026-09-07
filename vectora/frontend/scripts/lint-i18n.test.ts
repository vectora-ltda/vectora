import { describe, expect, it } from "vitest";
import { lintSource } from "./lint-i18n";

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

  it("accepts message calls and ignores technical attributes", () => {
    const violations = lintSource(
      'const label = m.common_save(); const dynamic = mDyn("common.save");\n' +
        '<button className="SaveButton" data-testid="save" title={label}>{label}</button>',
      "fixture.tsx",
    );
    expect(violations).toEqual([]);
  });
});
