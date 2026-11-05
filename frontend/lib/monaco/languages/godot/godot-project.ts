import type { EngineLanguageContribution } from "../contract";
import { GODOT_TOKENS as t } from "./tokens";
import { variantTokenizer } from "./variant";

export const godotProjectLanguage: EngineLanguageContribution = {
  id: "godot-project",
  aliases: ["Godot Project", "godot"],
  filenames: ["project.godot"],
  fenceAliases: ["godot", "project.godot"],
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
