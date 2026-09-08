export type TextDirection = "ltr" | "rtl";

/** Allowlist de locales RTL; desconhecidos permanecem LTR por segurança. */
export function directionForLocale(locale: string): TextDirection {
  return new Set(["ar", "fa", "he", "ur"]).has(
    locale.toLowerCase().split("-")[0],
  )
    ? "rtl"
    : "ltr";
}
