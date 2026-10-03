import { EventEmitter } from "node:events";
import type { DownloadItem } from "electron";
import { describe, expect, it, vi } from "vitest";
import { configureBrowserDownload } from "../browser-download";

describe("browser download lifecycle", () => {
  it.each(["completed", "cancelled", "interrupted"])(
    "publishes %s and retains explicit destination confirmation",
    (state) => {
      const item = Object.assign(new EventEmitter(), {
        getFilename: () => "report.pdf",
        getReceivedBytes: () => 10,
        getTotalBytes: () => 20,
        setSaveDialogOptions: vi.fn(),
        setSavePath: vi.fn(),
      });
      const publish = vi.fn();
      configureBrowserDownload(
        item as unknown as DownloadItem,
        "downloads",
        publish,
      );
      expect(item.setSavePath).not.toHaveBeenCalled();
      expect(item.setSaveDialogOptions).toHaveBeenCalledWith(
        expect.objectContaining({ properties: ["showOverwriteConfirmation"] }),
      );
      item.emit("updated", {}, "interrupted");
      expect(publish).toHaveBeenLastCalledWith(
        expect.objectContaining({ state: "interrupted" }),
      );
      item.emit("done", {}, state);
      expect(publish).toHaveBeenLastCalledWith(
        expect.objectContaining({ state, filename: "report.pdf" }),
      );
    },
  );
});
