/**
 * settings-store — migração de fontScale* de % (80-150) pra px (13-24).
 * Preferências → Aparência: os 3 controles de escala de fonte (ui/chat/
 * markdown) passaram a ser armazenados em px, equivalentes ao range antigo
 * de percentual sobre a base de 16px (FONT_SCALE_BASE_PX).
 */

import { describe, expect, it } from "vitest";
import {
  FONT_SCALE_MIN,
  FONT_SCALE_MAX,
  FONT_SCALE_BASE_PX,
  migrateFontScaleValue,
} from "../settings-store";

describe("settings-store — migrateFontScaleValue", () => {
  it("converte um valor antigo em % (ex. 120) pro px equivalente", () => {
    expect(migrateFontScaleValue(120)).toBe(Math.round((120 / 100) * 16));
  });

  it("valor já em px (dentro do novo range) não é reconvertido (idempotente)", () => {
    expect(migrateFontScaleValue(18)).toBe(18);
  });

  it("valor fora do novo range mas indistinguível de px permanece só clampado (heurística de range)", () => {
    expect(migrateFontScaleValue(FONT_SCALE_MIN)).toBe(FONT_SCALE_MIN);
  });

  it("par de erro: valor não-numérico cai pro default (FONT_SCALE_BASE_PX) clampado", () => {
    expect(migrateFontScaleValue("abc")).toBe(FONT_SCALE_BASE_PX);
    expect(migrateFontScaleValue(undefined)).toBe(FONT_SCALE_BASE_PX);
    expect(migrateFontScaleValue(null)).toBe(FONT_SCALE_BASE_PX);
  });

  it("resultado da migração sempre respeita FONT_SCALE_MIN/MAX", () => {
    expect(migrateFontScaleValue(1000)).toBe(FONT_SCALE_MAX);
    expect(migrateFontScaleValue(-50)).toBeGreaterThanOrEqual(FONT_SCALE_MIN);
  });
});
