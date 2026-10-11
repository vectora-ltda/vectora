export interface EditorDiagnostic {
  line: number;
  message: string;
  severity: "error" | "warning";
}

export interface EditorServiceOptions {
  warningsAsErrors?: boolean;
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
  const normalized = text
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => line.replace(/[ \t]+$/g, ""))
    .join("\n");
  const extensionNeedsFinalNewline = ["md", "markdown", "yaml", "yml"].includes(
    extension ?? "",
  );
  return extensionNeedsFinalNewline && !normalized.endsWith("\n")
    ? `${normalized}\n`
    : normalized;
}

/** Lightweight diagnostics that run without a language server. */
export function lintEditorText(
  path: string,
  text: string,
  options: EditorServiceOptions = {},
): EditorDiagnostic[] {
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
  if (["js", "jsx", "ts", "tsx", "mjs", "cjs"].includes(extension ?? "")) {
    const stack: Array<{ token: string; line: number }> = [];
    const pairs: Record<string, string> = { "}": "{", ")": "(", "]": "[" };
    text.split("\n").forEach((line, index) => {
      const stripped = line.replace(/(['"`])(?:\\.|(?!\1).)*\1/g, "");
      for (const token of stripped) {
        if (["{", "(", "["].includes(token)) {
          stack.push({ token, line: index + 1 });
        } else if (token in pairs) {
          const open = stack.pop();
          if (!open || open.token !== pairs[token]) {
            diagnostics.push({
              line: index + 1,
              message: `Delimitador ${token} sem par correspondente`,
              severity: "error",
            });
          }
        }
      }
    });
    for (const open of stack) {
      diagnostics.push({
        line: open.line,
        message: `Delimitador ${open.token} sem fechamento`,
        severity: "error",
      });
    }
  }
  if (["md", "markdown"].includes(extension ?? "")) {
    text.split("\n").forEach((line, index) => {
      if (/^#{1,6}[^ #]/.test(line)) {
        diagnostics.push({
          line: index + 1,
          message: "Título Markdown precisa de um espaço após #",
          severity: "warning",
        });
      }
    });
  }
  if (["yaml", "yml"].includes(extension ?? "")) {
    text.split("\n").forEach((line, index) => {
      if (/^\s*[^#\s][^:]*$/.test(line) && line.trim() !== "-") {
        diagnostics.push({
          line: index + 1,
          message: "Entrada YAML precisa de ':' ou marcador de lista",
          severity: "error",
        });
      }
    });
  }
  return options.warningsAsErrors
    ? diagnostics.map((diagnostic) =>
        diagnostic.severity === "warning"
          ? { ...diagnostic, severity: "error" }
          : diagnostic,
      )
    : diagnostics;
}

export function hasBlockingDiagnostics(
  diagnostics: readonly EditorDiagnostic[],
  warningsAsErrors: boolean,
): boolean {
  return diagnostics.some(
    (diagnostic) =>
      diagnostic.severity === "error" ||
      (warningsAsErrors && diagnostic.severity === "warning"),
  );
}
