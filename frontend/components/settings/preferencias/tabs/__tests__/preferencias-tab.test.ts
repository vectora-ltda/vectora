// @vitest-environment jsdom
/**
 * Testes para `themeLabel` — resolve o texto exibido no trigger do Select de
 * tema explicitamente, em vez de depender do registro interno do Radix
 * Select (que deixava o trigger em branco no primeiro paint, antes do
 * `SelectItem` correspondente montar).
 */

import { describe, expect, it } from "vitest";
import { m } from "@/lib/paraglide/messages";
import { THEME_PRESETS } from "@/lib/theme/presets";
import { themeLabel } from "../preferencias-tab";

describe("themeLabel", () => {
  it("resolve 'system' e 'custom' para os rótulos traduzidos", () => {
    expect(themeLabel("system")).toBe(m.prefs_theme_system());
    expect(themeLabel("custom")).toBe(m.prefs_theme_palette_custom());
  });

  it("resolve o id de um preset real pro seu label", () => {
    const preset = THEME_PRESETS[0];
    expect(themeLabel(preset.id)).toBe(preset.label);
  });

  it("id desconhecido (nunca deve acontecer, mas não pode ficar em branco) cai no próprio id", () => {
    expect(themeLabel("preset-que-nao-existe")).toBe("preset-que-nao-existe");
  });
});
