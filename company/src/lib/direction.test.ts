import { describe, expect, it } from "vitest";
import { directionForLocale } from "./direction";

describe("directionForLocale", () => {
  it("keeps supported locales LTR", () => {
    expect(directionForLocale("pt")).toBe("ltr");
    expect(directionForLocale("en-US")).toBe("ltr");
  });

  it("supports RTL fixtures without publishing a locale", () => {
    expect(directionForLocale("ar-test")).toBe("rtl");
    expect(directionForLocale("fa")).toBe("rtl");
  });

  it("fails closed for unknown values", () => {
    expect(directionForLocale("unknown")).toBe("ltr");
  });
});
