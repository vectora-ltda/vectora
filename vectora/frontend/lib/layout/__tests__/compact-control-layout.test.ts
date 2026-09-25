import { describe, expect, it } from "vitest";
import { balanceCompactControlWidths } from "../compact-control-layout";

describe("balanceCompactControlWidths", () => {
  it("reduz primeiro o controle mais largo", () => {
    const widths = balanceCompactControlWidths([180, 100, 40], 250);
    expect(widths[0]).toBeCloseTo(110);
    expect(widths[1]).toBe(100);
    expect(widths[2]).toBe(40);
  });

  it("reduz os dois maiores antes de tocar no menor", () => {
    const widths = balanceCompactControlWidths([180, 100, 40], 190);
    expect(widths[0]).toBeCloseTo(75);
    expect(widths[1]).toBeCloseTo(75);
    expect(widths[2]).toBeCloseTo(40);
  });

  it("só reduz os três juntos quando atingem o mesmo teto", () => {
    const widths = balanceCompactControlWidths([180, 100, 40], 100);
    expect(widths[0]).toBeCloseTo(100 / 3);
    expect(widths[1]).toBeCloseTo(100 / 3);
    expect(widths[2]).toBeCloseTo(100 / 3);
  });

  it("preserva a largura natural quando há espaço suficiente", () => {
    expect(balanceCompactControlWidths([180, 100, 40], 400)).toEqual([
      180, 100, 40,
    ]);
  });

  it("redistribui o espaço liberado por um rótulo menor", () => {
    expect(balanceCompactControlWidths([100, 40, 100], 240)).toEqual([
      100, 40, 100,
    ]);
    expect(balanceCompactControlWidths([100, 40, 100], 190)).toEqual([
      75, 40, 75,
    ]);
  });

  it("não reserva texto quando só cabem as partes fixas", () => {
    expect(balanceCompactControlWidths([100, 40, 100], 0)).toEqual([0, 0, 0]);
  });
});
