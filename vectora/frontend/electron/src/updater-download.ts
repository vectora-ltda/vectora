export type UpdateDownload = () => Promise<void>;

/** Starts an update after the optional snapshot settles without hiding updater errors. */
export function startUpdateDownload(
  pendingBackupPromise: Promise<void> | null,
  downloadUpdate: UpdateDownload,
  warn: (message: string, error: unknown) => void = (message, error) =>
    console.warn(message, error),
): Promise<void> {
  return (pendingBackupPromise ?? Promise.resolve())
    .catch((error: unknown) => {
      warn(
        "[updater] backup local indisponível; prosseguindo sem backup",
        error,
      );
    })
    .then(downloadUpdate);
}
