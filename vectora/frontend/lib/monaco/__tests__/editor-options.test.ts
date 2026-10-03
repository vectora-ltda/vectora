import { describe, expect, it } from "vitest";
import { godotEditorOptions } from "../editor-options";

describe("godotEditorOptions", () => {
  it("aplica as preferências configuráveis do File System", () => {
    const options = godotEditorOptions(14, "Test Mono", false, {
      minimap: false,
      fontLigatures: true,
      glyphMargin: false,
      bracketPairGuides: false,
      insertSpaces: false,
      parameterHints: false,
      cursorStyle: "block",
      tabSize: 4,
    });

    expect(options.minimap).toMatchObject({ enabled: false });
    expect(options.fontLigatures).toBe(true);
    expect(options.glyphMargin).toBe(false);
    expect(options.guides).toMatchObject({ bracketPairs: false });
    expect(options.insertSpaces).toBe(false);
    expect(options.parameterHints).toMatchObject({ enabled: false });
    expect(options.cursorStyle).toBe("block");
    expect(options.tabSize).toBe(4);
  });
});
