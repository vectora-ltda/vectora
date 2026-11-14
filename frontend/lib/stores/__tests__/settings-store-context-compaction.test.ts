// @vitest-environment jsdom
/**
 * settings-store — contextCompactionEnabled (Preferências → Contexto da
 * conversa). Vai em cada envio do chat como `config.context_compaction_enabled`;
 * é uma preferência local do aparelho, persistida entre sessões.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { useSettingsStore } from "../settings-store";

beforeEach(() => {
  window.localStorage.clear();
  useSettingsStore.getState().resetSettings();
});

describe("settings-store — contextCompactionEnabled", () => {
  it("vem ligada por padrão", () => {
    expect(useSettingsStore.getState().contextCompactionEnabled).toBe(true);
  });

  it("o setter liga e desliga", () => {
    useSettingsStore.getState().setContextCompactionEnabled(false);
    expect(useSettingsStore.getState().contextCompactionEnabled).toBe(false);

    useSettingsStore.getState().setContextCompactionEnabled(true);
    expect(useSettingsStore.getState().contextCompactionEnabled).toBe(true);
  });

  it("fica salva no armazenamento local para a próxima sessão", () => {
    useSettingsStore.getState().setContextCompactionEnabled(false);

    const keys = Array.from({ length: window.localStorage.length }, (_, i) =>
      window.localStorage.key(i),
    );
    const stored = keys
      .map((key) => (key ? window.localStorage.getItem(key) : null))
      .find((value) => value?.includes("contextCompactionEnabled"));
    expect(stored).toBeDefined();
    expect(JSON.parse(stored as string).state.contextCompactionEnabled).toBe(
      false,
    );
  });

  it("resetSettings volta ao padrão", () => {
    useSettingsStore.getState().setContextCompactionEnabled(false);

    useSettingsStore.getState().resetSettings();

    expect(useSettingsStore.getState().contextCompactionEnabled).toBe(true);
  });
});
