export type TextDirection = "ltr" | "rtl";

/** Allowlist de locales RTL; desconhecidos permanecem LTR por segurança. */
export function directionForLocale(locale: string): TextDirection {
  return new Set(["ar", "fa", "he", "ur"]).has(
    locale.toLowerCase().split("-")[0],
  )
    ? "rtl"
    : "ltr";
}

/** Aplica os atributos de idioma e direção quando o documento está disponível. */
export function applyDocumentDirection(locale: string): TextDirection {
  const direction = directionForLocale(locale);
  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("lang", locale);
    document.documentElement.setAttribute("dir", direction);
  }
  return direction;
}
