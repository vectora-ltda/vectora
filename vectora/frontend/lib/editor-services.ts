export interface EditorDiagnostic {
  line: number;
  message: string;
  severity: "error" | "warning";
}

/** Monaco buffers use LF and no BOM; encoding belongs exclusively to disk serialization. */
export function normalizeEditorText(text: string): string {
  return text.replace(/^\ufeff/, "").replace(/\r\n|\r/g, "\n");
}

/** Deterministic local formatter used by the Files workbench. */
export function formatEditorText(path: string, text: string): string {
  text = normalizeEditorText(text);
  const extension = path.toLowerCase().split(".").pop();
  if (extension === "json" || extension === "jsonc") {
    const errors: ParseError[] = [];
    parse(text, errors, {
      disallowComments: extension === "json",
      allowTrailingComma: extension === "jsonc",
    });
    if (errors.length) return text;
    return `${applyEdits(text, format(text, undefined, { tabSize: 2, insertSpaces: true, eol: "\n" })).trimEnd()}\n`;
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
    const errors: ParseError[] = [];
    parse(text, errors, {
      disallowComments: extension === "json",
      allowTrailingComma: extension === "jsonc",
    });
    for (const error of errors) {
      diagnostics.push({
        line: Math.max(1, text.slice(0, error.offset).split("\n").length),
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
import { applyEdits, format, parse, type ParseError } from "jsonc-parser";
