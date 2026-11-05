/**
 * PDF Preview Utility (F2)
 *
 * Renderiza a primeira página de um PDF (em base64) como data URL PNG usando
 * pdfjs-dist. O worker é servido estaticamente em /pdf.worker.min.mjs para
 * evitar dependência de CDN externo e garantir compatibilidade com Next.js.
 *
 * Uso:
 *   const dataUrl = await renderPdfFirstPage(base64String);
 *   // → "data:image/png;base64,..."
 */

let workerConfigured = false;

/**
 * Inicializa o GlobalWorkerOptions uma única vez.
 * Lazy-init evita erro "window is not defined" em SSR.
 */
async function ensureWorker(): Promise<typeof import("pdfjs-dist")> {
  const pdfjs = await import("pdfjs-dist");
  if (!workerConfigured) {
    pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
    workerConfigured = true;
  }
  return pdfjs;
}

/**
 * Converte base64 puro (sem prefixo data URL) em Uint8Array.
 */
function base64ToUint8Array(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

/**
 * Renderiza a primeira página do PDF e retorna uma data URL PNG.
 *
 * @param base64 - Conteúdo do PDF em base64 puro (sem "data:application/pdf;base64,")
 * @param scale  - Escala de renderização (default: 0.6 → ~tamanho de thumbnail)
 * @returns data URL da imagem PNG da primeira página
 */
export async function renderPdfFirstPage(
  base64: string,
  scale = 0.6,
): Promise<string> {
  const pdfjs = await ensureWorker();

  const data = base64ToUint8Array(base64);
  const pdf = await pdfjs.getDocument({ data }).promise;
  const page = await pdf.getPage(1);

  const viewport = page.getViewport({ scale });

  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("renderPdfFirstPage: canvas 2D não disponível");

  canvas.width = viewport.width;
  canvas.height = viewport.height;

  await page.render({ canvasContext: ctx, viewport, canvas }).promise;

  return canvas.toDataURL("image/png");
}
