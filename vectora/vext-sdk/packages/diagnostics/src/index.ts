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
