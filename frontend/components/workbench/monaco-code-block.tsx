"use client";

import { useEffect, useState } from "react";
import { colorizeLines } from "@/lib/monaco/colorize";
import { useMonacoTheme } from "@/lib/monaco/use-monaco-theme";

export function MonacoCodeBlock({
  code,
  language,
}: {
  code: string;
  language: string;
}) {
  const theme = useMonacoTheme(language);
  const [lines, setLines] = useState<string[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    void colorizeLines(code, language).then((result) => {
      if (!cancelled) setLines(result);
    });
    return () => {
      cancelled = true;
    };
  }, [code, language]);
  if (!lines)
    return <pre className="m-0 whitespace-pre-wrap break-words">{code}</pre>;
  return (
    <pre
      className="m-0 whitespace-pre-wrap break-words"
      data-monaco-theme={theme}
    >
      {lines.map((line, index) => (
        <span key={index} dangerouslySetInnerHTML={{ __html: line }} />
      ))}
    </pre>
  );
}
