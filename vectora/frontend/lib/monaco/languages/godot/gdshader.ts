import type { EngineLanguageContribution } from "../contract";
import { GODOT_TOKENS as t } from "./tokens";

export const gdshaderLanguage: EngineLanguageContribution = {
  id: "gdshader",
  aliases: ["Godot Shader", "gdshader"],
  extensions: [".gdshader", ".gdshaderinc"],
  fenceAliases: ["gdshader", "gdshaderinc"],
  configuration: {
    comments: { lineComment: "//", blockComment: ["/*", "*/"] },
    brackets: [
      ["{", "}"],
      ["[", "]"],
      ["(", ")"],
    ],
    autoClosingPairs: [
      { open: "{", close: "}" },
      { open: "[", close: "]" },
      { open: "(", close: ")" },
      { open: '"', close: '"' },
    ],
    folding: { offSide: false },
  },
  tokenizer: {
    keywords: [
      "shader_type",
      "render_mode",
      "uniform",
      "varying",
      "void",
      "if",
      "else",
      "for",
      "while",
      "return",
      "true",
      "false",
    ],
    tokenizer: {
      root: [
        [/\/\/.*$/, t.comment],
        [/\/\*/, { token: t.comment, next: "@comment" }],
        [/^\s*#(?:include|define)\b.*$/, t.keywordDirective],
        [/\b(?:shader_type|render_mode|uniform|varying)\b/, t.keyword],
        [/\b(?:if|else|for|while|return)\b/, t.keywordControl],
        [
          /\b(?:vec2|vec3|vec4|mat2|mat3|mat4|float|int|bool|sampler2D)\b/,
          t.typeBuiltin,
        ],
        [/\b(?:TIME|UV|VERTEX|NORMAL|ALBEDO|COLOR|SCREEN_UV)\b/, t.typeEngine],
        [/\b(?:hint_range|source_color|hint_normal)\b/, t.annotation],
        [/[A-Za-z_]\w*(?=\s*\()/, t.function],
        [/[A-Za-z_]\w*/, t.variable],
        [/\d+(?:\.\d+)?/, t.number],
        [/"[^"\n]*"/, t.string],
        [/[{}()[\]]/, t.bracket],
        [/[+*/%=<>!&|^~?:-]+/, t.operator],
        [/[;,.,]/, t.delimiter],
      ],
      comment: [
        [/[^/*]+/, t.comment],
        [/\/\*/, { token: t.comment, next: "@push" }],
        [/\*\//, { token: t.comment, next: "@pop" }],
        [/[/]/, t.comment],
      ],
    },
  },
};
