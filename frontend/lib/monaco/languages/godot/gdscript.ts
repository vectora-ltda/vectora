import type { EngineLanguageContribution } from "../contract";
import { GODOT_TOKENS as t } from "./tokens";
import {
  GODOT_GDSCRIPT_BUILTIN_TYPES,
  GODOT_GDSCRIPT_CONTROL_FLOW,
  GODOT_GDSCRIPT_RESERVED_WORDS,
  GODOT_GDSCRIPT_SOURCE_CONTRACT,
} from "./gdscript-contract";
export const gdscriptLanguage: EngineLanguageContribution = {
  id: "gdscript",
  aliases: ["GDScript", "gd"],
  extensions: [".gd"],
  fenceAliases: ["gdscript", "gd"],
  configuration: {
    comments: { lineComment: "#", blockComment: ["##", "##"] },
    brackets: [
      ["[", "]"],
      ["{", "}"],
      ["(", ")"],
    ],
    autoClosingPairs: [
      { open: "[", close: "]" },
      { open: "{", close: "}" },
      { open: "(", close: ")" },
      { open: '"', close: '"' },
      { open: "'", close: "'" },
    ],
    surroundingPairs: [
      { open: "[", close: "]" },
      { open: "{", close: "}" },
      { open: "(", close: ")" },
      { open: '"', close: '"' },
      { open: "'", close: "'" },
    ],
    folding: { offSide: true },
    indentationRules: {
      increaseIndentPattern: /:\s*(?:#.*)?$/,
      decreaseIndentPattern: /^\s*(?:elif|else|except|finally)\b/,
    },
  },
  tokenizer: {
    keywords: GODOT_GDSCRIPT_RESERVED_WORDS,
    controls: GODOT_GDSCRIPT_CONTROL_FLOW,
    builtinTypes: GODOT_GDSCRIPT_BUILTIN_TYPES,
    globalFunctions: ["assert", "preload"],
    tokenizer: {
      root: [
        [/^\s*##.*$/, t.commentDoc],
        [/#.*$/, t.comment],
        [/@[A-Za-z_]\w*/, t.annotation],
        [/\$[A-Za-z_][\w/.-]*/, t.nodeReference],
        [
          /[%&^]"[^"\n]*"/,
          {
            cases: {
              "^%.*": t.nodeReference,
              "^&.*": t.stringName,
              "^^.*": t.stringNodePath,
            },
          },
        ],
        [/\b(?:assert|preload)(?=\s*\()/, t.functionGlobal],
        [/\bfunc\b/, t.keyword],
        [/(?<=\bfunc\s+)[A-Za-z_]\w*(?=\s*\()/, t.functionDefinition],
        [/[A-Za-z_]\w*(?=\s*\()/, t.function],
        [/\b(?:signal)\s+([A-Za-z_]\w*)/, ["keyword", t.functionSignal]],
        [/(\.)([A-Za-z_]\w*)/, [t.delimiter, t.variableMember]],
        [
          /[A-Z][A-Za-z0-9_]*/,
          {
            cases: { "@builtinTypes": t.typeBuiltin, "@default": t.typeEngine },
          },
        ],
        [
          /[A-Za-z_]\w*/,
          {
            cases: {
              "@controls": t.keywordControl,
              "@keywords": t.keyword,
              "@builtinTypes": t.typeBuiltin,
              "@default": t.variable,
            },
          },
        ],
        [/0[bB][01_]+|0[xX][\da-fA-F_]+|\d[\d_]*(?:\.\d[\d_]*)?/, t.number],
        [/'''|"""/, { token: t.string, next: "@tripleString" }],
        [/['"]/, { token: t.string, next: "@string" }],
        [/[{}()[\]]/, t.bracket],
        [/[+\-*/%=<>!&|^~?:]+/, t.operator],
        [/[,.;]/, t.delimiter],
      ],
      string: [
        [/\\./, t.string],
        [/%[sdif]|\{[A-Za-z_]\w*\}/, t.stringPlaceholder],
        [/[^\\"']+/, t.string],
        [/['"]/, { token: t.string, next: "@pop" }],
      ],
      tripleString: [
        [/\\./, t.string],
        [/[^\\]+/, t.string],
        [/'''|"""/, { token: t.string, next: "@pop" }],
      ],
    },
  },
  source: GODOT_GDSCRIPT_SOURCE_CONTRACT,
};
