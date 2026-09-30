import type * as Monaco from "monaco-editor";

/** Opções visuais e de navegação inspiradas no CodeEdit do Godot. */
export function godotEditorOptions(
  fontSize: number,
  fontFamily: string,
  readOnly: boolean,
): Monaco.editor.IStandaloneEditorConstructionOptions {
  return {
    readOnly,
    domReadOnly: readOnly,
    fontSize,
    fontFamily,
    // O editor da Godot reserva um pouco mais de respiro entre as linhas;
    // manter a proporção explícita evita depender do default do Monaco.
    lineHeight: Math.round(fontSize * 1.7),
    fontLigatures: false,
    // Mantém o comportamento padrão do editor da Godot: Ctrl/Cmd + roda
    // ajusta o zoom do código sem interferir na rolagem normal.
    mouseWheelZoom: true,
    minimap: {
      enabled: !readOnly,
      showSlider: "always",
      renderCharacters: true,
      maxColumn: 80,
      scale: 1,
    },
    lineNumbers: "on",
    glyphMargin: true,
    folding: true,
    foldingHighlight: true,
    showFoldingControls: "mouseover",
    renderLineHighlight: "line",
    renderWhitespace: "selection",
    guides: {
      indentation: true,
      highlightActiveIndentation: true,
      bracketPairs: true,
    },
    bracketPairColorization: { enabled: false },
    stickyScroll: { enabled: false },
    smoothScrolling: true,
    scrollBeyondLastLine: false,
    scrollbar: {
      verticalScrollbarSize: 12,
      horizontalScrollbarSize: 12,
      useShadows: false,
    },
    overviewRulerBorder: false,
    automaticLayout: true,
    tabSize: 2,
    wordWrap: "off",
  };
}
