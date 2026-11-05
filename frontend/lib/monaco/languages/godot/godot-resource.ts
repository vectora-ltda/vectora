import type { EngineLanguageContribution } from "../contract";
import { GODOT_TOKENS as t } from "./tokens";
import { variantTokenizer } from "./variant";

export const godotResourceLanguage: EngineLanguageContribution = {
  id: "godot-resource",
  aliases: ["Godot Resource", "tscn", "tres"],
  extensions: [".tscn", ".tres"],
  fenceAliases: ["tscn", "tres"],
  configuration: {
    comments: { lineComment: ";" },
    brackets: [
      ["[", "]"],
      ["(", ")"],
      ["{", "}"],
    ],
    autoClosingPairs: [
      { open: "[", close: "]" },
      { open: "{", close: "}" },
      { open: "(", close: ")" },
      { open: '"', close: '"' },
    ],
    folding: { markers: { start: /^\s*\[/, end: /$/ } },
  },
  tokenizer: {
    ...variantTokenizer,
    tokenizer: {
      root: [
        [/^\s*[A-Za-z_][\w/]*(?=\s*=)/, t.property],
        ...variantTokenizer.tokenizer.root,
      ],
    },
  },
};
