// @vitest-environment jsdom
/**
 * Painéis resizáveis persistem largura em px (`chatSidebarWidth`,
 * `splitSize`) sem clamp contra a viewport — uma largura salva numa tela
 * larga sobrevive ao reload numa tela estreita e causa overflow horizontal.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";

import { useClampPanelWidths } from "@/lib/hooks/use-clamp-panel-widths";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { useWorkbenchStore } from "@/lib/stores/workbench-store";

function setInnerWidth(width: number) {
  Object.defineProperties(window, {
    innerWidth: {
      configurable: true,
      writable: true,
      value: width,
    },
    // getUnscaledViewportWidth intentionally uses outerWidth first so the
    // tests must update both values when simulating a resized window.
    outerWidth: {
      configurable: true,
      writable: true,
      value: width,
    },
  });
}

beforeEach(() => {
  setInnerWidth(1024);
});

afterEach(() => {
  useSettingsStore.getState().setChatSidebarWidth(256);
  useWorkbenchStore.getState().setSplitSize(224);
});

describe("useClampPanelWidths", () => {
  it("não mexe nas larguras quando cabem na viewport", () => {
    useSettingsStore.getState().setChatSidebarWidth(300);
    useWorkbenchStore.getState().setSplitSize(300);

    renderHook(() => useClampPanelWidths());

    expect(useSettingsStore.getState().chatSidebarWidth).toBe(300);
    expect(useWorkbenchStore.getState().splitSize).toBe(300);
  });

  it("clampa chatSidebarWidth/splitSize quando a viewport é menor que a largura salva", () => {
    // chatSidebarWidth tem teto próprio de 480 (setChatSidebarWidth) — usa
    // esse teto como "largura salva bem maior que a viewport" em vez de 700,
    // que o próprio setter já rejeitaria antes do hook entrar em jogo.
    setInnerWidth(400);
    useSettingsStore.getState().setChatSidebarWidth(480);
    useWorkbenchStore.getState().setSplitSize(700);

    renderHook(() => useClampPanelWidths());

    expect(useSettingsStore.getState().chatSidebarWidth).toBeLessThan(400);
    expect(useWorkbenchStore.getState().splitSize).toBeLessThan(400);
  });

  it("viewport extremamente estreita (menor que a reserva mínima) não lança nem zera", () => {
    setInnerWidth(200);
    useSettingsStore.getState().setChatSidebarWidth(480);

    expect(() => renderHook(() => useClampPanelWidths())).not.toThrow();
    // Sem espaço nem pro conteúdo principal — a largura salva não é
    // sobrescrita por um valor negativo/zero, fica como está.
    expect(useSettingsStore.getState().chatSidebarWidth).toBe(480);
  });

  it("re-clampa ao disparar o evento de resize", () => {
    useSettingsStore.getState().setChatSidebarWidth(300);
    renderHook(() => useClampPanelWidths());
    expect(useSettingsStore.getState().chatSidebarWidth).toBe(300);

    setInnerWidth(500);
    window.dispatchEvent(new Event("resize"));

    expect(useSettingsStore.getState().chatSidebarWidth).toBeLessThan(300);
  });
});
