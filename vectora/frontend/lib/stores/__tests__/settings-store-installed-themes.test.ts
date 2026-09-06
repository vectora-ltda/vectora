/**
 * settings-store — lista de temas instalados via VS Code Marketplace
 * (`installedThemes`), que substitui o slot único de `customThemeColors`
 * pra suportar múltiplos temas instalados pelo usuário.
 */

// @vitest-environment jsdom
import { describe, expect, it, beforeEach } from "vitest";
import { migrateInstalledThemes, useSettingsStore } from "../settings-store";
import type { ThemePresetDef } from "@/lib/theme/presets";

const sampleTheme: ThemePresetDef = {
  id: "vscode-publisher.tema",
  label: "Tema de Exemplo",
  mode: "dark",
  family: "vscode:publisher:tema",
  colors: {
    background: "#111111",
    foreground: "#eeeeee",
    card: "#161616",
    border: "#222222",
    primary: "#00ff88",
    accent: "#ff8800",
    muted: "#333333",
    sidebar: "#0d0d0d",
    userBubble: "#004488",
  },
};

beforeEach(() => {
  useSettingsStore.setState({ installedThemes: [] });
});

describe("settings-store — installedThemes", () => {
  it("ignora entradas nulas e cores incompletas", () => {
    const themes = migrateInstalledThemes([
      null,
      {
        id: "vscode-invalid-background",
        label: "Invalid background",
        colors: { background: 123 },
      },
    ]);

    expect(themes).toHaveLength(0);
  });

  it("addInstalledTheme adiciona um tema à lista", () => {
    useSettingsStore.getState().addInstalledTheme(sampleTheme);

    expect(useSettingsStore.getState().installedThemes).toEqual([sampleTheme]);
  });

  it("addInstalledTheme com id repetido substitui o anterior, não duplica", () => {
    useSettingsStore.getState().addInstalledTheme(sampleTheme);
    useSettingsStore
      .getState()
      .addInstalledTheme({ ...sampleTheme, label: "Tema Atualizado" });

    const themes = useSettingsStore.getState().installedThemes;
    expect(themes).toHaveLength(1);
    expect(themes[0]!.label).toBe("Tema Atualizado");
  });

  it("removeInstalledTheme remove só o tema pelo id", () => {
    const other: ThemePresetDef = { ...sampleTheme, id: "vscode-outro.tema" };
    useSettingsStore.getState().addInstalledTheme(sampleTheme);
    useSettingsStore.getState().addInstalledTheme(other);

    useSettingsStore.getState().removeInstalledTheme(sampleTheme.id);

    const themes = useSettingsStore.getState().installedThemes;
    expect(themes).toEqual([other]);
  });

  it("erro/borda — remover um id inexistente não quebra nem altera a lista", () => {
    useSettingsStore.getState().addInstalledTheme(sampleTheme);

    useSettingsStore.getState().removeInstalledTheme("nao-existe");

    expect(useSettingsStore.getState().installedThemes).toEqual([sampleTheme]);
  });
});
