/** Converte o resultado da ponte Electron em anexo PNG ou cancelamento. */
export function screenshotBytesToFile(
  bytes: Uint8Array | null,
  timestamp = Date.now(),
): File | null {
  if (!bytes) return null;
  return new File([new Uint8Array(bytes)], `screenshot-${timestamp}.png`, {
    type: "image/png",
  });
}

export async function captureScreenshotAttachment(
  capture: () => Promise<Uint8Array | null>,
  processFiles: (files: File[]) => Promise<void>,
  onError: () => void,
): Promise<void> {
  try {
    const file = screenshotBytesToFile(await capture());
    if (file) await processFiles([file]);
  } catch {
    onError();
  }
}
