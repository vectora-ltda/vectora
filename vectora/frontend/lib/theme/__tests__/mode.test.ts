import { describe, expect, it } from "vitest";
import { classifyMode, contrastRatio } from "../mode";

describe("classifyMode", () => {
  it("classifica fundos v�lidos por lumin�ncia", () => {
    expect(classifyMode({ background: "#ffffff" })).toBe("light");
    expect(classifyMode({ background: "#000000" })).toBe("dark");
    expect(classifyMode({ background: "#b0b0b0" })).toBe("dark");
  });

  it.each([
    undefined,
    "",
    "rgba(255, 255, 255, 0.5)",
    "transparent",
    "white",
    "#fff",
    "invalid",
  ])("trata %s como dark quando n�o � um hex confi�vel", (background) =>
    expect(classifyMode({ background: background ?? "" })).toBe("dark"),
  );
});

describe("contrastRatio", () => {
  it("calcula contraste de texto preto e branco", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeGreaterThan(20);
  });
});
