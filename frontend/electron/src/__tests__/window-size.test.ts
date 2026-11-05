import { describe, it, expect } from "vitest";
import { computeDefaultWindowSize } from "../window-size";

describe("computeDefaultWindowSize", () => {
  it("escala pra ~62% de uma tela 1080p, bem menor que a tela cheia", () => {
    const size = computeDefaultWindowSize({ width: 1920, height: 1080 });
    expect(size).toEqual({ width: 1190, height: 670 });
    expect(size.width).toBeLessThan(1920 * 0.7);
    expect(size.height).toBeLessThan(1080 * 0.7);
  });

  it("capa no mínimo usável numa tela pequena e no máximo numa tela grande (par de borda)", () => {
    const small = computeDefaultWindowSize({ width: 1024, height: 768 });
    expect(small).toEqual({ width: 960, height: 600 });

    const huge = computeDefaultWindowSize({ width: 3840, height: 2160 });
    expect(huge).toEqual({ width: 1280, height: 720 });
  });
});
