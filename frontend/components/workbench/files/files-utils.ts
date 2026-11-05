/** Helpers puros do Files workbench. */

/** MIME type customizado usado no `dataTransfer` do drag-and-drop da árvore
 * — evita colidir com drags de fora do app (ex. arrastar um arquivo do
 * explorador do SO, que usa `Files`/`text/uri-list`, nunca este tipo). */
export const FS_DRAG_MIME = "application/x-vectora-fs-path";

/** Tom (cor) do badge git por status porcelain. */
export const GIT_BADGE_TONE: Record<string, string> = {
  M: "text-amber-500",
  A: "text-green-500",
  D: "text-destructive",
  R: "text-blue-400",
  "?": "text-muted-foreground",
};

/** Normaliza separadores para "/" (backend devolve POSIX). */
export function norm(path: string): string {
  return path.replace(/\\/g, "/");
}

/** Formata data ISO 8601 em string compacta (dd/mm/yyyy).
 * `new Date()` nunca lança para entrada inválida — produz Invalid Date com
 * getters NaN — por isso a validade é checada explicitamente via getTime(). */
export function fmtDate(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
    return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`;
  } catch {
    return iso.slice(0, 10);
  }
}
