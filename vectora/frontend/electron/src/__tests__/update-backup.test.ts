import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  createRotatingUpdateBackup,
  restoreUpdateBackup,
} from "../update-backup.js";

describe("update backups", () => {
  it("keeps at most five rotation entries", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "vectora-update-"));
    const userData = path.join(root, "user-data");
    const backups = path.join(root, "backups");
    await mkdir(userData, { recursive: true });
    await writeFile(path.join(userData, "settings.json"), "safe");

    for (let index = 0; index < 6; index += 1) {
      await createRotatingUpdateBackup(userData, backups, `0.1.${index}`);
    }
    const entries = await import("../update-backup.js").then(
      ({ listUpdateBackups }) => listUpdateBackups(backups),
    );
    expect(entries).toHaveLength(5);
  });

  it("rejects a renderer-supplied backup outside the root", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "vectora-update-"));
    await expect(
      restoreUpdateBackup(
        {
          id: "x",
          createdAt: "",
          appVersion: "",
          path: path.join(root, "outside"),
          bytes: 0,
          sha256: "",
        },
        path.join(root, "user-data"),
        path.join(root, "backups"),
      ),
    ).rejects.toThrow("fora da área permitida");
  });
});
