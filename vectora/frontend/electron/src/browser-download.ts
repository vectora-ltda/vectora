import type { DownloadItem } from "electron";
import path from "node:path";
import type { BrowserDownloadEvent } from "./preload.js";

/** Keep Electron's save dialog and overwrite confirmation instead of silently reusing a filename. */
export function configureBrowserDownload(
  item: DownloadItem,
  directory: string,
  publish: (event: Omit<BrowserDownloadEvent, "id" | "profileId">) => void,
): void {
  const filename = path.basename(item.getFilename()) || "download";
  item.setSaveDialogOptions({
    defaultPath: path.join(directory, filename),
    properties: ["showOverwriteConfirmation"],
  });
  const emit = (state: BrowserDownloadEvent["state"]) =>
    publish({
      filename,
      state,
      receivedBytes: item.getReceivedBytes(),
      totalBytes: item.getTotalBytes(),
    });
  emit("progressing");
  item.on("updated", (_event, state) => emit(state));
  item.once("done", (_event, state) => emit(state));
}
