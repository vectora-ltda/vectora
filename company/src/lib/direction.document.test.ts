// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { applyDocumentDirection } from "./direction";

describe("applyDocumentDirection", () => {
  afterEach(() => {
    document.documentElement.removeAttribute("lang");
    document.documentElement.removeAttribute("dir");
  });

  it.each(["pt", "en", "es", "fr", "it", "de", "ru"])(
    "mantém %s em LTR",
    (locale) => {
      expect(applyDocumentDirection(locale)).toBe("ltr");
      expect(document.documentElement).toHaveAttribute("lang", locale);
      expect(document.documentElement).toHaveAttribute("dir", "ltr");
    },
  );

  it("aplica RTL à fixture sem publicar o locale", () => {
    expect(applyDocumentDirection("ar-test")).toBe("rtl");
    expect(document.documentElement).toHaveAttribute("lang", "ar-test");
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
  });

  it("volta para LTR ao trocar de locale no cliente", () => {
    applyDocumentDirection("ar-test");
    applyDocumentDirection("pt");
    expect(document.documentElement).toHaveAttribute("lang", "pt");
    expect(document.documentElement).toHaveAttribute("dir", "ltr");
  });
});
