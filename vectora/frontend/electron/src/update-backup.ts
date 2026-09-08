import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";

export interface UpdateBackupEntry {
  id: string;
  createdAt: string;
  appVersion: string;
  path: string;
  bytes: number;
  sha256: string;
}

const MANIFEST = "manifest.json";
const EXCLUDED = new Set([
  "Cache",
  "Code Cache",
  "GPUCache",
  "logs",
  "tokens",
  "secrets",
]);

async function copySafe(
  source: string,
  destination: string,
): Promise<{ bytes: number; hash: string }> {
  const data = await fs.readFile(source);
  await fs.mkdir(path.dirname(destination), { recursive: true });
  await fs.writeFile(destination, data, { mode: 0o600 });
  return {
    bytes: data.byteLength,
    hash: createHash("sha256").update(data).digest("hex"),
  };
}

export async function createRotatingUpdateBackup(
  userData: string,
  backupRoot: string,
  appVersion: string,
  maxBackups = 5,
): Promise<UpdateBackupEntry> {
  const id = `${new Date().toISOString().replace(/[:.]/g, "-")}-${appVersion}`;
  const destination = path.join(backupRoot, id);
  await fs.mkdir(destination, { recursive: true });
  const files: string[] = [];
  for (const name of await fs.readdir(userData)) {
    if (EXCLUDED.has(name) || name.endsWith(".lock")) continue;
    const source = path.join(userData, name);
    const stat = await fs.stat(source);
    if (stat.isFile() && stat.size <= 256 * 1024 * 1024) {
      files.push(name);
      await copySafe(source, path.join(destination, name));
    }
  }
  const manifest: UpdateBackupEntry = {
    id,
    createdAt: new Date().toISOString(),
    appVersion,
    path: destination,
    bytes: (
      await Promise.all(
        files.map((name) => fs.stat(path.join(destination, name))),
      )
    ).reduce((sum, item) => sum + item.size, 0),
    sha256: createHash("sha256").update(files.join("\n")).digest("hex"),
  };
  await fs.writeFile(
    path.join(destination, MANIFEST),
    JSON.stringify(manifest, null, 2),
    { mode: 0o600 },
  );
  const entries = (await fs.readdir(backupRoot)).sort().reverse();
  await Promise.all(
    entries
      .slice(maxBackups)
      .map((entry) =>
        fs.rm(path.join(backupRoot, entry), { recursive: true, force: true }),
      ),
  );
  return manifest;
}

export async function listUpdateBackups(
  backupRoot: string,
): Promise<UpdateBackupEntry[]> {
  const entries: UpdateBackupEntry[] = [];
  for (const name of await fs.readdir(backupRoot).catch(() => [])) {
    try {
      const raw = await fs.readFile(
        path.join(backupRoot, name, MANIFEST),
        "utf8",
      );
      entries.push(JSON.parse(raw) as UpdateBackupEntry);
    } catch {
      // Ignore incomplete backup directories left by an interrupted copy.
    }
  }
  return entries.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export async function restoreUpdateBackup(
  entry: UpdateBackupEntry,
  userData: string,
  backupRoot?: string,
): Promise<void> {
  const resolvedPath = path.resolve(entry.path);
  if (
    backupRoot &&
    !resolvedPath.startsWith(`${path.resolve(backupRoot)}${path.sep}`)
  ) {
    throw new Error("Backup fora da área permitida");
  }
  const manifest = JSON.parse(
    await fs.readFile(path.join(resolvedPath, MANIFEST), "utf8"),
  ) as UpdateBackupEntry;
  if (
    manifest.id !== entry.id ||
    path.dirname(path.resolve(manifest.path)) !== path.dirname(resolvedPath)
  )
    throw new Error("Backup inválido");
  const rollback = `${userData}.rollback-${Date.now()}`;
  await fs
    .cp(userData, rollback, { recursive: true, errorOnExist: false })
    .catch(() => undefined);
  try {
    for (const name of await fs.readdir(resolvedPath)) {
      if (name === MANIFEST || EXCLUDED.has(name)) continue;
      const source = path.join(resolvedPath, name);
      const stat = await fs.lstat(source);
      if (!stat.isFile()) throw new Error("Backup contém entrada não regular");
      await fs.copyFile(source, path.join(userData, name));
    }
  } catch (error) {
    await fs.rm(userData, { recursive: true, force: true });
    await fs.cp(rollback, userData, { recursive: true });
    throw error;
  } finally {
    await fs
      .rm(rollback, { recursive: true, force: true })
      .catch(() => undefined);
  }
}
