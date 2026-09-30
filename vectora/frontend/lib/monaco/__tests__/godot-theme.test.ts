import { describe, expect, it, vi } from "vitest";
import {
  GODOT_DARK_THEME,
  GODOT_LIGHT_THEME,
  godotDarkTheme,
  godotThemeFor,
  registerGodotLanguage,
  registerGodotMonacoTheme,
  resolveMonacoTheme,
  vectoraDarkTheme,
} from "../godot-theme";
import { GODOT_PALETTES } from "../godot-theme";

function createMonacoMock() {
  return {
    editor: { defineTheme: vi.fn() },
    languages: {
      getLanguages: vi.fn(() => []),
      register: vi.fn(),
      setMonarchTokensProvider: vi.fn(),
    },
  };
}

describe("Godot Monaco theme", () => {
  it("keeps local identifiers neutral like Godot's text editor", () => {
    expect(GODOT_PALETTES.dark.variable).toBe("#d6d6d6");
    expect(godotDarkTheme.rules).toEqual(
      expect.arrayContaining([
        { token: "identifier", foreground: "d6d6d6" },
        { token: "variable.local", foreground: "d6d6d6" },
      ]),
    );
  });

  it("does not let the reduced Vectora theme inherit Monaco blue for variables", () => {
    expect(vectoraDarkTheme.rules).toEqual(
      expect.arrayContaining([
        { token: "variable", foreground: "d6d6d6" },
        { token: "identifier", foreground: "d6d6d6" },
      ]),
    );
  });
  it("selects a stable theme for each appearance mode", () => {
    expect(godotThemeFor(true)).toBe(GODOT_DARK_THEME);
    expect(godotThemeFor(false)).toBe(GODOT_LIGHT_THEME);
  });

  it("uses the Godot editor theme automatically for Godot languages", () => {
    expect(
      resolveMonacoTheme({
        presetId: "default-dark",
        isDark: true,
        language: "gdscript",
      }),
    ).toBe(GODOT_DARK_THEME);
    expect(
      resolveMonacoTheme({
        presetId: "default-light",
        isDark: false,
        language: "godot-resource",
      }),
    ).toBe(GODOT_LIGHT_THEME);
  });

  it("registers both themes and the GDScript language", () => {
    const monaco = createMonacoMock();
    registerGodotMonacoTheme(monaco as never);
    registerGodotLanguage(monaco as never);
    expect(monaco.editor.defineTheme).toHaveBeenCalledTimes(2);
    expect(monaco.languages.register).toHaveBeenCalledWith({
      id: "gdscript",
      extensions: [".gd"],
    });
    expect(monaco.languages.setMonarchTokensProvider).toHaveBeenCalledWith(
      "gdscript",
      expect.objectContaining({ tokenizer: expect.any(Object) }),
    );
  });
});
