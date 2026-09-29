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
  number: string;
  annotation: string;
  nodePath: string;
  nodeReference: string;
  stringName: string;
  property: string;
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
    keyword: "#ff7085",
    controlFlow: "#ff8ccc",
    type: "#42ffc2",
    engineType: "#8fffff",
    userType: "#c7ffed",
    function: "#57b2ff",
    functionDefinition: "#66e6ff",
    globalFunction: "#a3a3f5",
    variable: "#d6d6d6",
    string: "#ffeca1",
    number: "#a1ffe0",
    annotation: "#ffb273",
    nodePath: "#b7c47d",
    nodeReference: "#63c25a",
    stringName: "#ffc2a6",
    property: "#8ed1d1",
    operator: "#d4d4d4",
    selection: "#365a78",
    lineHighlight: "#282828",
    border: "#303030",
    error: "#f26d78",
    warning: "#e7b84b",
    info: "#70b7ff",
    whitespace: "#454545",
  },
  light: {
    background: "#f5f5f5",
    foreground: "#343434",
    comment: "#6b6b6b",
    docComment: "#262666",
    keyword: "#e62282",
    controlFlow: "#bd1ecc",
    type: "#009933",
    engineType: "#1c8c66",
    userType: "#2e7366",
    function: "#0039e6",
    functionDefinition: "#009999",
    globalFunction: "#5c2eb8",
    variable: "#343434",
    string: "#996b00",
    number: "#008c47",
    annotation: "#cc5e00",
    nodePath: "#2e8c00",
    nodeReference: "#008000",
    stringName: "#cc8f73",
    property: "#0066ad",
    operator: "#343434",
    selection: "#c8ddf0",
    lineHighlight: "#e9edf2",
    border: "#d8dce2",
    error: "#c62828",
    warning: "#8a5a00",
    info: "#005cc5",
    whitespace: "#c8ccd2",
  },
};

const rules = (p: GodotMonacoPalette): Monaco.editor.ITokenThemeRule[] => [
  { token: "comment", foreground: p.comment.slice(1) },
  {
    token: "comment.doc",
    foreground: p.docComment.slice(1),
    fontStyle: "italic",
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
  { token: "string.placeholder", foreground: p.annotation.slice(1) },
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
  { token: "delimiter", foreground: p.foreground.slice(1) },
  { token: "delimiter.bracket", foreground: p.foreground.slice(1) },
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
  presetId,
  isDark,
}: {
  presetId?: string;
  isDark: boolean;
}): string {
  if (presetId === GODOT_DARK_THEME || presetId === "godot-dark")
    return GODOT_DARK_THEME;
  if (presetId === GODOT_LIGHT_THEME || presetId === "godot-light")
    return GODOT_LIGHT_THEME;
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
