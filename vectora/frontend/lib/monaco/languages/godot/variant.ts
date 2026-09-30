import type * as Monaco from "monaco-editor";
import { GODOT_TOKENS as t } from "./tokens";

export const variantTokenizer: Pick<
  Monaco.languages.IMonarchLanguage,
  "keywords" | "tokenizer"
> = {
  keywords: [
    "true",
    "false",
    "null",
    "Vector2",
    "Vector3",
    "Color",
    "ExtResource",
    "SubResource",
    "NodePath",
    "preload",
  ],
  tokenizer: {
    root: [
      [/;.*$/, t.comment],
      [/\b(?:true|false|null)\b/, t.keyword],
      [
        /\b(?:ExtResource|SubResource|Vector2|Vector3|Vector4|Color|NodePath|Transform2D|Transform3D)\b/,
        t.typeEngine,
      ],
      [/"[^"\n]*"|'[^'\n]*'/, t.string],
      [/&"[^"\n]*"/, t.stringName],
      [/\^"[^"\n]*"/, t.stringNodePath],
      [/\d+(?:\.\d+)?/, t.number],
      [/[A-Za-z_][\w/]*/, t.property],
      [/[{}()[\]]/, t.bracket],
      [/[=:,]/, t.delimiter],
    ],
  },
};
