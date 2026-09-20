/**
 * Tests para os ajustes de sidebar do settings-store: clamp de largura
 * (240–480, arredondado) e posição.
 */

import { describe, expect, it, beforeEach } from "vitest";
import { useSettingsStore } from "../settings-store";

beforeEach(() => {
  useSettingsStore.getState().resetSettings();
});

describe("settings-store — sidebar", () => {
  it("setSidebarWidth respeita o mínimo (240)", () => {
    useSettingsStore.getState().setSidebarWidth(50);
    expect(useSettingsStore.getState().sidebarWidth).toBe(240);
  });

  it("setSidebarWidth respeita o máximo (480)", () => {
    useSettingsStore.getState().setSidebarWidth(9999);
    expect(useSettingsStore.getState().sidebarWidth).toBe(480);
  });

  it("setSidebarWidth arredonda valores fracionários", () => {
    useSettingsStore.getState().setSidebarWidth(250.7);
    expect(useSettingsStore.getState().sidebarWidth).toBe(251);
  });

  it("setSidebarWidth mantém valores dentro da faixa", () => {
    useSettingsStore.getState().setSidebarWidth(300);
    expect(useSettingsStore.getState().sidebarWidth).toBe(300);
  });

  it("setSidebarPosition alterna entre left e right", () => {
    useSettingsStore.getState().setSidebarPosition("right");
    expect(useSettingsStore.getState().sidebarPosition).toBe("right");
    useSettingsStore.getState().setSidebarPosition("left");
    expect(useSettingsStore.getState().sidebarPosition).toBe("left");
  });
});

describe("settings-store — chatSidebarWidth (IDE mode)", () => {
  it("valor padrão é 300", () => {
    expect(useSettingsStore.getState().chatSidebarWidth).toBe(300);
  });

  it("setChatSidebarWidth atualiza o valor", () => {
    useSettingsStore.getState().setChatSidebarWidth(400);
    expect(useSettingsStore.getState().chatSidebarWidth).toBe(400);
  });

  it("setChatSidebarWidth respeita mínimo (240)", () => {
    useSettingsStore.getState().setChatSidebarWidth(100);
    expect(useSettingsStore.getState().chatSidebarWidth).toBe(240);
  });

  it("setChatSidebarWidth respeita máximo (480)", () => {
    useSettingsStore.getState().setChatSidebarWidth(9999);
    expect(useSettingsStore.getState().chatSidebarWidth).toBe(480);
  });

  it("setChatSidebarWidth arredonda valores fracionários", () => {
    useSettingsStore.getState().setChatSidebarWidth(360.6);
    expect(useSettingsStore.getState().chatSidebarWidth).toBe(361);
  });

  it("resetSettings restaura para 300", () => {
    useSettingsStore.getState().setChatSidebarWidth(500);
    useSettingsStore.getState().resetSettings();
    expect(useSettingsStore.getState().chatSidebarWidth).toBe(300);
  });

  it("valor 240 (mínimo exato) é aceito sem clamp", () => {
    useSettingsStore.getState().setChatSidebarWidth(240);
    expect(useSettingsStore.getState().chatSidebarWidth).toBe(240);
  });

  it("valor 480 (máximo exato) é aceito sem clamp", () => {
    useSettingsStore.getState().setChatSidebarWidth(480);
    expect(useSettingsStore.getState().chatSidebarWidth).toBe(480);
  });
});
