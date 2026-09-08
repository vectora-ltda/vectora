export type SmartPasteKind = "url" | "json" | "yaml" | "code" | "text";

export interface SmartPasteResult {
  kind: SmartPasteKind;
  extension: string;
  mimeType: string;
}

const URL_RE = /^https?:\/\/[^\s]+$/i;

/** Classifica clipboard sem executar ou interpretar o conteúdo como código. */
export function classifySmartPaste(value: string): SmartPasteResult {
  const trimmed = value.trim();
  if (URL_RE.test(trimmed))
    return { kind: "url", extension: "txt", mimeType: "text/plain" };
  try {
    JSON.parse(trimmed);
    return { kind: "json", extension: "json", mimeType: "application/json" };
  } catch {
    // YAML is deliberately conservative: only mappings/lists with scalar values.
    if (
      /^(?:---\s*)?[\w.-]+\s*:\s*[^\n]*$/m.test(trimmed) ||
      /^-\s+\S+/m.test(trimmed)
    ) {
      return { kind: "yaml", extension: "yaml", mimeType: "text/yaml" };
    }
  }
  if (
    /\b(const|let|function|import|export)\b|[{}]\s*[;)]|<\w+[\s>]/.test(trimmed)
  ) {
    const extension = /\b(interface|type)\b|:\s*(string|number|boolean)\b/.test(
      trimmed,
    )
      ? "ts"
      : "js";
    return { kind: "code", extension, mimeType: "text/plain" };
  }
  return { kind: "text", extension: "txt", mimeType: "text/plain" };
}
