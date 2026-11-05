/**
 * settings-store — uiScalePercent substitui os 4 sliders de fonte
 * separados (ui/chat/markdown/monaco) por um único controle; internamente
 * ainda deriva os 4 campos legados (consumidos por __root.tsx/
 * message-item.tsx/markdown-view.tsx/monaco-readonly.tsx).
 */

// @vitest-environment jsdom
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import {
  useSettingsStore,
  loadUserSettings,
  getStorageKey,
  FONT_SCALE_BASE_PX,
} from "../settings-store";

beforeEach(() => {
  useSettingsStore.setState({
    uiScalePercent: 100,
    fontScaleUi: FONT_SCALE_BASE_PX,
    fontScaleChat: FONT_SCALE_BASE_PX,
    fontScaleMarkdown: FONT_SCALE_BASE_PX,
    monacoFontSize: 13,
  });
  delete window.vectora;
  localStorage.clear();
});

afterEach(() => {
  delete window.vectora;
  localStorage.clear();
});

describe("settings-store — uiScalePercent (modo navegador, sem window.vectora)", () => {
  it("125% deriva fontScaleUi/Chat/Markdown proporcionalmente", () => {
    useSettingsStore.getState().setUiScalePercent(125);

    const s = useSettingsStore.getState();
    expect(s.uiScalePercent).toBe(125);
    expect(s.fontScaleUi).toBe(Math.round((FONT_SCALE_BASE_PX * 125) / 100));
    expect(s.fontScaleChat).toBe(s.fontScaleUi);
    expect(s.fontScaleMarkdown).toBe(s.fontScaleUi);
  });

  it("monacoFontSize também escala proporcionalmente (base 13px)", () => {
    useSettingsStore.getState().setUiScalePercent(150);

    expect(useSettingsStore.getState().monacoFontSize).toBe(
      Math.round(13 * 1.5),
    );
  });

  it("erro/borda — valor fora do range [50, 200] é clampado", () => {
    useSettingsStore.getState().setUiScalePercent(9999);
    expect(useSettingsStore.getState().uiScalePercent).toBe(200);

    useSettingsStore.getState().setUiScalePercent(-50);
    expect(useSettingsStore.getState().uiScalePercent).toBe(50);
  });
});

describe("settings-store — uiScalePercent (desktop Electron, window.vectora.zoom presente)", () => {
  it("delega ao zoom nativo e mantém os campos derivados no tamanho base (evita escalar em dobro)", () => {
    const setPercent = vi.fn();
    window.vectora = { zoom: { setPercent, get: vi.fn() } };

    useSettingsStore.getState().setUiScalePercent(150);

    expect(setPercent).toHaveBeenCalledWith(150);
    const s = useSettingsStore.getState();
    expect(s.uiScalePercent).toBe(150);
    expect(s.fontScaleUi).toBe(FONT_SCALE_BASE_PX);
    expect(s.fontScaleChat).toBe(FONT_SCALE_BASE_PX);
    expect(s.fontScaleMarkdown).toBe(FONT_SCALE_BASE_PX);
    expect(s.monacoFontSize).toBe(13);
  });

  it("loadUserSettings reaplica o zoom nativo com o uiScalePercent reidratado do localStorage", async () => {
    // Escreve direto no localStorage um estado persistido com 150% — como
    // se uma sessão anterior (talvez ainda em modo navegador, sem zoom
    // nativo) tivesse salvo essa preferência antes do app reabrir.
    const key = getStorageKey();
    localStorage.setItem(
      key,
      JSON.stringify({ state: { uiScalePercent: 150 }, version: 4 }),
    );

    const setPercent = vi.fn();
    window.vectora = { zoom: { setPercent, get: vi.fn() } };

    loadUserSettings();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(setPercent).toHaveBeenCalledWith(150);
  });

  it("erro/borda — loadUserSettings não chama zoom nativo fora do Electron", async () => {
    const key = getStorageKey();
    localStorage.setItem(
      key,
      JSON.stringify({ state: { uiScalePercent: 150 }, version: 4 }),
    );
    delete window.vectora;

    loadUserSettings();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(window.vectora).toBeUndefined();
  });
});
