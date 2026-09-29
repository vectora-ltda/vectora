import { describe, expect, it } from "vitest";
import { gdscriptLanguage } from "../languages/godot/gdscript";
import {
  GODOT_GDSCRIPT_BUILTIN_TYPES,
  GODOT_GDSCRIPT_CONTROL_FLOW,
  GODOT_GDSCRIPT_RESERVED_WORDS,
} from "../languages/godot/gdscript-contract";

describe("Godot source contract", () => {
  it("keeps reserved words and control flow aligned with Godot 4", () => {
    expect(GODOT_GDSCRIPT_RESERVED_WORDS).toEqual(
      expect.arrayContaining([
        "class_name",
        "namespace",
        "trait",
        "when",
        "INF",
      ]),
    );
    expect(GODOT_GDSCRIPT_CONTROL_FLOW).toEqual(
      expect.arrayContaining(["break", "continue", "when", "while"]),
    );
    expect(GODOT_GDSCRIPT_CONTROL_FLOW).not.toEqual(
      expect.arrayContaining(["await", "yield"]),
    );
  });

  it("uses complete Variant types and records dynamic engine symbols", () => {
    expect(GODOT_GDSCRIPT_BUILTIN_TYPES).toEqual(
      expect.arrayContaining([
        "Rect2i",
        "Projection",
        "PackedVector4Array",
        "Variant",
        "void",
      ]),
    );
    expect(gdscriptLanguage.source?.files).toContain(
      "modules/gdscript/gdscript_tokenizer.cpp",
    );
    expect(gdscriptLanguage.source?.dynamicSymbols).toEqual(
      expect.arrayContaining([
        "ClassDB engine types",
        "GDScript utility functions",
      ]),
    );
  });

  it("keeps declaration keywords separate from function names", () => {
    const root = gdscriptLanguage.tokenizer.tokenizer?.root ?? [];
    expect(
      root.some((rule) => Array.isArray(rule) && rule[1] === "keyword"),
    ).toBe(true);
    expect(
      root.some(
        (rule) => Array.isArray(rule) && rule[1] === "function.definition",
      ),
    ).toBe(true);
  });
});
