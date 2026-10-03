export interface EditorDiagnostic {
  line: number;
  message: string;
  severity: "error" | "warning";
}

/** Deterministic local formatter used by the Files workbench. */
export function formatEditorText(path: string, text: string): string {
  const extension = path.toLowerCase().split(".").pop();
  if (extension === "json" || extension === "jsonc") {
    try {
      return `${JSON.stringify(JSON.parse(text), null, 2)}\n`;
    } catch {
      return text;
    }
  }
  return text
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => line.replace(/[ \t]+$/g, ""))
    .join("\n");
}

/** Lightweight diagnostics that run without a language server. */
export function lintEditorText(path: string, text: string): EditorDiagnostic[] {
  const diagnostics: EditorDiagnostic[] = [];
  const extension = path.toLowerCase().split(".").pop();
  if (extension === "json" || extension === "jsonc") {
    try {
      JSON.parse(text);
    } catch (error) {
      diagnostics.push({
        line: Math.max(
          1,
          text.slice(0, (error as SyntaxError).message.length).split("\n")
            .length,
        ),
        message: "JSON inválido",
        severity: "error",
      });
    }
  }
  text.split("\n").forEach((line, index) => {
    if (/[ \t]+$/.test(line)) {
      diagnostics.push({
        line: index + 1,
        message: "Espaços no fim da linha",
        severity: "warning",
      });
    }
  });
  return diagnostics;
}
