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
