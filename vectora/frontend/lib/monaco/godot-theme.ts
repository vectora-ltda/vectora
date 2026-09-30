import type * as Monaco from "monaco-editor";

export const GODOT_DARK_THEME = "vectora-godot-dark";
export const GODOT_LIGHT_THEME = "vectora-godot-light";
export const VECTOR_DARK_THEME = "vectora-dark";
export const VECTOR_LIGHT_THEME = "vectora-light";

export interface GodotMonacoPalette {
  background: string;
  foreground: string;
  comment: string;
  docComment: string;
  keyword: string;
  controlFlow: string;
  type: string;
  engineType: string;
  userType: string;
  function: string;
  functionDefinition: string;
  globalFunction: string;
  variable: string;
  string: string;
  stringPlaceholder: string;
  number: string;
  annotation: string;
  nodePath: string;
  nodeReference: string;
  stringName: string;
  property: string;
  symbol: string;
  operator: string;
  selection: string;
  lineHighlight: string;
  border: string;
  error: string;
  warning: string;
  info: string;
  whitespace: string;
}

export const GODOT_PALETTES: Record<"dark" | "light", GodotMonacoPalette> = {
  dark: {
    background: "#1f1f1f",
    foreground: "#d6d6d6",
    comment: "#6b6b6b",
    docComment: "#99b3cc",
    keyword: "#e06c7a",
    controlFlow: "#d889b2",
    type: "#64c7a7",
    engineType: "#79c7c7",
    userType: "#a8d6ca",
    function: "#6ea8d6",
    functionDefinition: "#72c2d0",
    globalFunction: "#9b9bc8",
    variable: "#d6d6d6",
    string: "#d8c98b",
    stringPlaceholder: "#d2a16a",
    number: "#91cbb5",
    annotation: "#d8a579",
    nodePath: "#a8b17c",
    nodeReference: "#77aa70",
    stringName: "#d2aa97",
    property: "#9bb5cf",
    symbol: "#9aaec9",
    operator: "#9aaec9",
    selection: "#365a78",
    lineHighlight: "#282828",
    border: "#303030",
    error: "#f26d78",
    warning: "#e7b84b",
    info: "#76a6cf",
    whitespace: "#454545",
  },
  light: {
    background: "#f5f5f5",
    foreground: "#343434",
    comment: "#6b6b6b",
    docComment: "#262666",
    keyword: "#c94a78",
    controlFlow: "#9b58a6",
    type: "#267a52",
    engineType: "#327a68",
    userType: "#467e73",
    function: "#3868ad",
    functionDefinition: "#327d7d",
    globalFunction: "#694e9e",
    variable: "#343434",
    string: "#876b18",
    stringPlaceholder: "#b97835",
    number: "#267a52",
    annotation: "#a76421",
    nodePath: "#4b791f",
    nodeReference: "#32703a",
    stringName: "#a87562",
    property: "#356b9f",
    symbol: "#435a9b",
    operator: "#343434",
    selection: "#c8ddf0",
    lineHighlight: "#e9edf2",
    border: "#d8dce2",
    error: "#c62828",
    warning: "#8a5a00",
    info: "#35659f",
    whitespace: "#c8ccd2",
  },
};

const rules = (p: GodotMonacoPalette): Monaco.editor.ITokenThemeRule[] => [
  { token: "comment", foreground: p.comment.slice(1) },
  {
    token: "comment.doc",
    foreground: p.docComment.slice(1),
  },
  { token: "keyword", foreground: p.keyword.slice(1) },
  {
    token: "keyword.control",
    foreground: p.controlFlow.slice(1),
  },
  { token: "keyword.directive", foreground: p.annotation.slice(1) },
  { token: "type", foreground: p.type.slice(1) },
  { token: "type.builtin", foreground: p.type.slice(1) },
  { token: "type.engine", foreground: p.engineType.slice(1) },
  { token: "type.user", foreground: p.userType.slice(1) },
  { token: "function", foreground: p.function.slice(1) },
  { token: "function.definition", foreground: p.functionDefinition.slice(1) },
  { token: "function.global", foreground: p.globalFunction.slice(1) },
  { token: "function.signal", foreground: p.functionDefinition.slice(1) },
  { token: "variable", foreground: p.variable.slice(1) },
  { token: "variable.local", foreground: p.variable.slice(1) },
  { token: "variable.readonly", foreground: p.variable.slice(1) },
  { token: "identifier", foreground: p.variable.slice(1) },
  { token: "identifier.local", foreground: p.variable.slice(1) },
  { token: "variable.member", foreground: p.property.slice(1) },
  { token: "variable.nodereference", foreground: p.nodeReference.slice(1) },
  { token: "string", foreground: p.string.slice(1) },
  { token: "string.placeholder", foreground: p.stringPlaceholder.slice(1) },
  { token: "string.name", foreground: p.stringName.slice(1) },
  { token: "string.nodepath", foreground: p.nodePath.slice(1) },
  { token: "number", foreground: p.number.slice(1) },
  { token: "annotation", foreground: p.annotation.slice(1) },
  { token: "property", foreground: p.property.slice(1) },
  {
    token: "metatag.section",
    foreground: p.annotation.slice(1),
    fontStyle: "bold",
  },
  { token: "operator", foreground: p.operator.slice(1) },
  { token: "delimiter", foreground: p.symbol.slice(1) },
  { token: "delimiter.bracket", foreground: p.symbol.slice(1) },
];

function themeData(
  mode: "dark" | "light",
  complete: boolean,
): Monaco.editor.IStandaloneThemeData {
  const p = GODOT_PALETTES[mode];
  return {
    base: mode === "dark" ? "vs-dark" : "vs",
    inherit: true,
    colors: complete
      ? {
          "editor.background": p.background,
          "editor.foreground": p.foreground,
          "editorLineNumber.foreground": p.comment,
          "editorLineNumber.activeForeground": p.foreground,
          "editorCursor.foreground": p.function,
          "editor.selectionBackground": p.selection,
          "editor.lineHighlightBackground": p.lineHighlight,
          "editorIndentGuide.background": p.border,
          "editorIndentGuide.activeBackground": p.info,
          "editorBracketMatch.background": p.selection,
          "editorBracketMatch.border": p.info,
          "editorError.foreground": p.error,
          "editorWarning.foreground": p.warning,
          "editorInfo.foreground": p.info,
          "editorGutter.background": p.background,
          "minimap.background": p.background,
          "editorOverviewRuler.errorForeground": p.error,
          "editorOverviewRuler.warningForeground": p.warning,
          "editorOverviewRuler.infoForeground": p.info,
          "editorWhitespace.foreground": p.whitespace,
          "editorRuler.foreground": p.border,
          "editorIndentGuide.background1": p.border,
          "editorIndentGuide.activeBackground1": p.info,
          "minimapSlider.background": `${p.foreground}22`,
          "minimapSlider.hoverBackground": `${p.foreground}44`,
          "minimapSlider.activeBackground": `${p.foreground}55`,
        }
      : {},
    rules: complete
      ? rules(p)
      : rules(p).filter((rule) =>
          [
            "comment",
            "comment.doc",
            "keyword",
            "keyword.control",
            "keyword.directive",
            "type",
            "type.builtin",
            "type.engine",
            "type.user",
            "function",
            "function.definition",
            "function.global",
            "function.signal",
            "variable",
            "variable.local",
            "variable.readonly",
            "identifier",
            "identifier.local",
            "variable.member",
            "variable.nodereference",
            "string",
            "string.placeholder",
            "string.name",
            "string.nodepath",
            "number",
            "annotation",
            "property",
            "operator",
            "delimiter",
            "delimiter.bracket",
          ].includes(rule.token),
        ),
  };
}

export const godotDarkTheme = themeData("dark", true);
export const godotLightTheme = themeData("light", true);
export const vectoraDarkTheme = themeData("dark", false);
export const vectoraLightTheme = themeData("light", false);

const registered = new WeakSet<object>();
export function ensureMonacoThemes(
  monaco: typeof import("monaco-editor"),
): void {
  if (registered.has(monaco)) return;
  monaco.editor.defineTheme(GODOT_DARK_THEME, godotDarkTheme);
  monaco.editor.defineTheme(GODOT_LIGHT_THEME, godotLightTheme);
  monaco.editor.defineTheme(VECTOR_DARK_THEME, vectoraDarkTheme);
  monaco.editor.defineTheme(VECTOR_LIGHT_THEME, vectoraLightTheme);
  registered.add(monaco);
}
export function resolveMonacoTheme({
  presetId: _presetId,
  isDark,
  language,
}: {
  presetId?: string;
  isDark: boolean;
  language?: string;
}): string {
  if (
    language === "gdscript" ||
    language === "gdshader" ||
    language === "godot-resource" ||
    language === "godot-project"
  )
    return isDark ? GODOT_DARK_THEME : GODOT_LIGHT_THEME;
  return isDark ? VECTOR_DARK_THEME : VECTOR_LIGHT_THEME;
}
export function godotThemeFor(isDark: boolean): string {
  return isDark ? GODOT_DARK_THEME : GODOT_LIGHT_THEME;
}
export function registerGodotMonacoTheme(
  monaco: typeof import("monaco-editor"),
): void {
  monaco.editor.defineTheme(GODOT_DARK_THEME, godotDarkTheme);
  monaco.editor.defineTheme(GODOT_LIGHT_THEME, godotLightTheme);
}
export function registerGodotLanguage(
  monaco: typeof import("monaco-editor"),
): void {
  if (
    monaco.languages
      .getLanguages()
      .some((language) => language.id === "gdscript")
  )
    return;
  monaco.languages.register({ id: "gdscript", extensions: [".gd"] });
  monaco.languages.setMonarchTokensProvider("gdscript", {
    tokenizer: {
      root: [
        [/#.*$/, "comment"],
        [/@[A-Za-z_]\w*/, "annotation"],
        [/[A-Za-z_]\w*(?=\s*\()/, "function"],
        [/[A-Za-z_]\w*/, "identifier"],
        [/\d+/, "number"],
        [/['\"]/, { token: "string", next: "@string" }],
      ],
      string: [
        [/[^'\"]+/, "string"],
        [/['\"]/, { token: "string", next: "@pop" }],
      ],
    },
  });
}
