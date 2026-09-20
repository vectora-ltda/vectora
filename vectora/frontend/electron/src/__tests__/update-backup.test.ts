import { mkdtemp, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import {
  createRotatingUpdateBackup,
  restoreUpdateBackup,
  withFileLockRetry,
} from "../update-backup.js";
import { startUpdateDownload } from "../updater-download.js";

describe("update backups", () => {
  it("retries transient Windows file locks before succeeding", async () => {
    const lockError = Object.assign(new Error("file is busy"), {
      code: "EBUSY",
    });
    let attempts = 0;
    await expect(
      withFileLockRetry(async () => {
        attempts += 1;
        if (attempts < 3) throw lockError;
        return "snapshot-ready";
      }),
    ).resolves.toBe("snapshot-ready");
    expect(attempts).toBe(3);
  });

  it("continues downloading when the optional backup rejects", async () => {
    const downloadUpdate = vi.fn<() => Promise<void>>().mockResolvedValue();
    const warn = vi.fn();
    const backupError = new Error("locked user data");

    await startUpdateDownload(
      Promise.reject(backupError),
      downloadUpdate,
      warn,
    );

    expect(warn).toHaveBeenCalledWith(
      "[updater] backup local indisponível; prosseguindo sem backup",
      backupError,
    );
    expect(downloadUpdate).toHaveBeenCalledOnce();
  });

  it("keeps updater download failures observable", async () => {
    const downloadError = new Error("download failed");
    const downloadUpdate = vi
      .fn<() => Promise<void>>()
      .mockRejectedValue(downloadError);

    await expect(startUpdateDownload(null, downloadUpdate)).rejects.toBe(
      downloadError,
    );
  });

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
          files: [],
        },
        path.join(root, "user-data"),
        path.join(root, "backups"),
      ),
    ).rejects.toThrow("fora da área permitida");
  });

  it("recursively restaura diretórios e remove estado criado depois", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "vectora-update-"));
    const userData = path.join(root, "user-data");
    const backups = path.join(root, "backups");
    await mkdir(path.join(userData, "Local Storage"), { recursive: true });
    await writeFile(path.join(userData, "Local Storage", "state"), "before");
    const entry = await createRotatingUpdateBackup(userData, backups, "0.1.0");
    await writeFile(path.join(userData, "Local Storage", "state"), "after");
    await mkdir(path.join(userData, "new-directory"), { recursive: true });
    await writeFile(path.join(userData, "new-directory", "new-file"), "new");

    await restoreUpdateBackup(entry, userData, backups);

    await expect(
      readFile(path.join(userData, "Local Storage", "state"), "utf8"),
    ).resolves.toBe("before");
    await expect(
      readFile(path.join(userData, "new-directory", "new-file"), "utf8"),
    ).rejects.toThrow();
  });

  it("não copia a árvore de backups para um snapshot posterior", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "vectora-update-"));
    const userData = path.join(root, "user-data");
    const backups = path.join(userData, "update-backups");
    await mkdir(userData, { recursive: true });
    await writeFile(path.join(userData, "settings.json"), "safe");
    await createRotatingUpdateBackup(userData, backups, "0.1.0");
    const second = await createRotatingUpdateBackup(userData, backups, "0.1.1");

    expect(
      second.files.some((file) => file.path.startsWith("update-backups")),
    ).toBe(false);
    await expect(
      readdir(path.join(second.path, "update-backups")),
    ).rejects.toThrow();
  });

  it("recusa conteúdo corrompido antes de promover a restauração", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "vectora-update-"));
    const userData = path.join(root, "user-data");
    const backups = path.join(root, "backups");
    await mkdir(userData, { recursive: true });
    await writeFile(path.join(userData, "settings.json"), "safe");
    const entry = await createRotatingUpdateBackup(userData, backups, "0.1.0");
    await writeFile(path.join(entry.path, "settings.json"), "tampered");

    await expect(restoreUpdateBackup(entry, userData, backups)).rejects.toThrow(
      /Integridade do backup inválida|Backup contém arquivo inválido/,
    );
    await expect(
      readFile(path.join(userData, "settings.json"), "utf8"),
    ).resolves.toBe("safe");
  });

  it("serializa restaurações concorrentes sem deixar estado intermediário", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "vectora-update-"));
    const userData = path.join(root, "user-data");
    const backups = path.join(root, "backups");
    await mkdir(userData, { recursive: true });
    await writeFile(path.join(userData, "settings.json"), "first");
    const first = await createRotatingUpdateBackup(userData, backups, "0.1.0");
    await writeFile(path.join(userData, "settings.json"), "second");
    const second = await createRotatingUpdateBackup(userData, backups, "0.1.1");
    await writeFile(path.join(userData, "settings.json"), "current");

    await Promise.all([
      restoreUpdateBackup(first, userData, backups),
      restoreUpdateBackup(second, userData, backups),
    ]);

    await expect(
      readFile(path.join(userData, "settings.json"), "utf8"),
    ).resolves.toMatch(/^(first|second)$/);
    const leftovers = (await readdir(root)).filter(
      (name) =>
        name.startsWith("user-data.restore-") ||
        name.startsWith("user-data.rollback-"),
    );
    expect(leftovers).toEqual([]);
  });
});
