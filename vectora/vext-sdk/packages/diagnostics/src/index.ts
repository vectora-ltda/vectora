export type DiagnosticSeverity = "error" | "warning" | "info" | "hint";
export interface Diagnostic {
  uri: string;
  message: string;
  severity: DiagnosticSeverity;
  range: { start: number; end: number };
  source?: string;
  code?: string;
}
export interface DiagnosticCollection {
  set(uri: string, diagnostics: readonly Diagnostic[]): void;
  clear(): void;
}

export class MemoryDiagnosticCollection implements DiagnosticCollection {
  private readonly values = new Map<string, readonly Diagnostic[]>();
  set(uri: string, diagnostics: readonly Diagnostic[]): void {
    this.values.set(uri, [...diagnostics]);
  }
  clear(): void {
    this.values.clear();
  }
  get(uri: string): readonly Diagnostic[] {
    return this.values.get(uri) ?? [];
  }
  all(): ReadonlyMap<string, readonly Diagnostic[]> {
    return this.values;
  }
}

export function diagnosticFromLine(
  line: string,
  source = "process",
): Diagnostic | undefined {
  const match =
    /^(.*?):(\d+)(?::(\d+))?:\s*(error|warning|info|hint)?:?\s*(.*)$/i.exec(
      line.trim(),
    );
  if (!match || !match[5]) return undefined;
  const severity = (match[4]?.toLowerCase() ?? "error") as DiagnosticSeverity;
  return {
    uri: match[1],
    message: match[5],
    severity,
    source,
    range: { start: Number(match[2]), end: Number(match[2]) },
  };
}
