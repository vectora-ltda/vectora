import type * as Monaco from "monaco-editor";

export interface EditorPreferences {
  minimap?: boolean;
  wordWrap?: boolean;
  formatOnType?: boolean;
  quickSuggestions?: boolean;
  lineNumbers?: boolean;
  tabSize?: number;
  renderWhitespace?: "none" | "selection" | "all";
  stickyScroll?: boolean;
  smoothScrolling?: boolean;
  fontLigatures?: boolean;
  glyphMargin?: boolean;
  bracketPairGuides?: boolean;
  insertSpaces?: boolean;
  parameterHints?: boolean;
  cursorStyle?: "line" | "block" | "underline";
}

/** Opções visuais e de navegação inspiradas no CodeEdit do Godot. */
export function godotEditorOptions(
  fontSize: number,
  fontFamily: string,
  readOnly: boolean,
  preferences: EditorPreferences = {},
): Monaco.editor.IStandaloneEditorConstructionOptions {
  return {
    readOnly,
    domReadOnly: readOnly,
    fontSize,
    fontFamily,
    // O editor da Godot reserva um pouco mais de respiro entre as linhas;
    // manter a proporção explícita evita depender do default do Monaco.
    lineHeight: Math.round(fontSize * 1.7),
    fontLigatures: preferences.fontLigatures ?? false,
    // Mantém o comportamento padrão do editor da Godot: Ctrl/Cmd + roda
    // ajusta o zoom do código sem interferir na rolagem normal.
    mouseWheelZoom: true,
    minimap: {
      enabled: !readOnly && (preferences.minimap ?? true),
      showSlider: "always",
      renderCharacters: true,
      maxColumn: 80,
      scale: 1,
    },
    lineNumbers: preferences.lineNumbers === false ? "off" : "on",
    glyphMargin: preferences.glyphMargin ?? true,
    folding: true,
    foldingHighlight: true,
    showFoldingControls: "mouseover",
    renderLineHighlight: "line",
    renderWhitespace: preferences.renderWhitespace ?? "selection",
    guides: {
      indentation: true,
      highlightActiveIndentation: true,
      bracketPairs: preferences.bracketPairGuides ?? true,
    },
    bracketPairColorization: { enabled: false },
    stickyScroll: { enabled: preferences.stickyScroll ?? false },
    smoothScrolling: preferences.smoothScrolling ?? true,
    scrollBeyondLastLine: false,
    scrollbar: {
      verticalScrollbarSize: 12,
      horizontalScrollbarSize: 12,
      useShadows: false,
    },
    overviewRulerBorder: false,
    automaticLayout: true,
    formatOnType: preferences.formatOnType ?? true,
    quickSuggestions: preferences.quickSuggestions ?? true,
    parameterHints: { enabled: preferences.parameterHints ?? true },
    cursorStyle: preferences.cursorStyle ?? "line",
    insertSpaces: preferences.insertSpaces ?? true,
    tabSize: preferences.tabSize ?? 2,
    wordWrap: preferences.wordWrap ? "on" : "off",
  };
}
