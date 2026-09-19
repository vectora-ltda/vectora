"use client";

/**
 * MonacoReadOnly — editor Monaco read-only, carregado sob demanda.
 *
 * Isolado em um módulo próprio para ser importado via `React.lazy` pelo
 * FileViewer: o `monaco-editor` depende de `window` e quebra em ambientes
 * sem DOM (testes/SSR). Mantê-lo fora do grafo de import estático do viewer
 * evita esse acoplamento — só carrega no cliente quando há texto a exibir.
 */

import MonacoEditor from "@monaco-editor/react";
import { Loader2 } from "lucide-react";

import { languageFromPath } from "@/lib/monaco/setup";
import { useSettingsStore } from "@/lib/stores/settings-store";

export default function MonacoReadOnly({
  value,
  path,
  isDark,
  diffColors = false,
}: {
  value: string;
  path: string;
  isDark: boolean;
  diffColors?: boolean;
}) {
  const monacoFontSize = useSettingsStore((s) => s.monacoFontSize);
  return (
    <MonacoEditor
      value={value}
      language={languageFromPath(path)}
      theme={isDark ? "vs-dark" : "vs"}
      options={{
        readOnly: true,
        domReadOnly: true,
        fontSize: monacoFontSize,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 2,
        wordWrap: "on",
      }}
      onMount={(editor) => {
        if (!diffColors) return;
        const decorations = value.split("\n").flatMap((line, index) => {
          const className =
            line.startsWith("+") && !line.startsWith("+++")
              ? "vectora-diff-added-line"
              : line.startsWith("-") && !line.startsWith("---")
                ? "vectora-diff-removed-line"
                : line.startsWith("@@")
                  ? "vectora-diff-hunk-line"
                  : null;
          if (!className) return [];
          return [
            {
              range: {
                startLineNumber: index + 1,
                startColumn: 1,
                endLineNumber: index + 1,
                endColumn: 1,
              },
              options: { isWholeLine: true, className },
            },
          ];
        });
        editor.createDecorationsCollection(decorations);
      }}
      height="100%"
      loading={
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      }
    />
  );
}
