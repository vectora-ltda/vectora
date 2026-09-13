/* oxlint-disable -- preserve backup ordering semantics. */
import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";
import type {
  UpdateBackupEntry,
  UpdateBackupFile,
} from "./update-backup-types.js";
export type {
  UpdateBackupEntry,
  UpdateBackupFile,
} from "./update-backup-types.js";

const MANIFEST = "manifest.json";
const MAX_FILE_BYTES = 256 * 1024 * 1024;
const EXCLUDED = new Set([
  "Cache",
  "Code Cache",
  "GPUCache",
  "logs",
  "tokens",
  "secrets",
  "update-backups",
]);
let snapshotQueue: Promise<void> = Promise.resolve();

function isExcluded(relativePath: string): boolean {
  return relativePath
    .split(path.sep)
    .some((component) => EXCLUDED.has(component));
}

function digestTree(files: readonly UpdateBackupFile[]): string {
  const hash = createHash("sha256");
  for (const file of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
    hash.update(`${file.path}\0${file.bytes}\0${file.sha256}\n`);
  }
  return hash.digest("hex");
}

async function collectFiles(root: string, current = root): Promise<string[]> {
  const result: string[] = [];
  for (const name of await fs.readdir(current)) {
    const relative = path.relative(root, path.join(current, name));
    if (isExcluded(relative) || name.endsWith(".lock")) continue;
    const source = path.join(current, name);
    const stat = await fs.lstat(source);
    if (stat.isSymbolicLink()) throw new Error("userData contém symlink");
    if (stat.isDirectory()) result.push(...(await collectFiles(root, source)));
    else if (stat.isFile() && stat.size <= MAX_FILE_BYTES)
      result.push(relative);
  }
  return result;
}

async function copySafe(
  source: string,
  destination: string,
): Promise<UpdateBackupFile> {
  const stat = await fs.lstat(source);
  if (!stat.isFile() || stat.isSymbolicLink())
    throw new Error("entrada não regular");
  if (stat.size > MAX_FILE_BYTES) throw new Error("arquivo excede o limite");
  const data = await fs.readFile(source);
  await fs.mkdir(path.dirname(destination), { recursive: true });
  await fs.writeFile(destination, data, { mode: 0o600 });
  return {
    path: "",
    bytes: data.byteLength,
    sha256: createHash("sha256").update(data).digest("hex"),
  };
}

async function withSnapshotLock<T>(operation: () => Promise<T>): Promise<T> {
  const previous = snapshotQueue;
  let release!: () => void;
  snapshotQueue = new Promise<void>((resolve) => {
    release = resolve;
  });
  await previous;
  try {
    return await operation();
  } finally {
    release();
  }
}

export async function createRotatingUpdateBackup(
  userData: string,
  backupRoot: string,
  appVersion: string,
  maxBackups = 5,
): Promise<UpdateBackupEntry> {
  return withSnapshotLock(async () => {
    const id = `${new Date().toISOString().replace(/[:.]/g, "-")}-${appVersion}`;
    const temporary = path.join(backupRoot, `.tmp-${id}-${process.pid}`);
    const destination = path.join(backupRoot, id);
    await fs.mkdir(temporary, { recursive: true });
    try {
      const files: UpdateBackupFile[] = [];
      for (const relative of await collectFiles(userData)) {
        const copied = await copySafe(
          path.join(userData, relative),
          path.join(temporary, relative),
        );
        files.push({ ...copied, path: relative });
      }
      const manifest: UpdateBackupEntry = {
        id,
        createdAt: new Date().toISOString(),
        appVersion,
        path: destination,
        bytes: files.reduce((sum, file) => sum + file.bytes, 0),
        sha256: digestTree(files),
        files,
      };
      await fs.writeFile(
        path.join(temporary, MANIFEST),
        JSON.stringify(manifest, null, 2),
        { mode: 0o600 },
      );
      await fs.rename(temporary, destination);
      const entries = (await fs.readdir(backupRoot))
        .filter((entry) => !entry.startsWith(".tmp-"))
        .sort()
        .reverse();
      await Promise.all(
        entries.slice(maxBackups).map((entry) =>
          fs.rm(path.join(backupRoot, entry), {
            recursive: true,
            force: true,
          }),
        ),
      );
      return manifest;
    } catch (error) {
      await fs
        .rm(temporary, { recursive: true, force: true })
        .catch(() => undefined);
      throw error;
    }
  });
}

export async function listUpdateBackups(
  backupRoot: string,
): Promise<UpdateBackupEntry[]> {
  const entries: UpdateBackupEntry[] = [];
  for (const name of await fs.readdir(backupRoot).catch(() => [])) {
    try {
      const candidate = path.join(backupRoot, name);
      const stat = await fs.lstat(candidate);
      if (
        !stat.isDirectory() ||
        stat.isSymbolicLink() ||
        name.startsWith(".tmp-")
      )
        continue;
      const entry = JSON.parse(
        await fs.readFile(path.join(candidate, MANIFEST), "utf8"),
      ) as UpdateBackupEntry;
      if (entry.files?.length && digestTree(entry.files) === entry.sha256)
        entries.push(entry);
    } catch {
      /* diretório temporário ou snapshot incompleto */
    }
  }
  return entries.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

async function restoreUpdateBackupUnlocked(
  entry: UpdateBackupEntry,
  userData: string,
  backupRoot?: string,
): Promise<void> {
  const resolvedPath = path.resolve(entry.path);
  if (backupRoot) {
    let rootReal: string;
    let snapshotReal: string;
    try {
      const rootStat = await fs.lstat(backupRoot);
      const snapshotStat = await fs.lstat(resolvedPath);
      if (!rootStat.isDirectory() || rootStat.isSymbolicLink())
        throw new Error("Backup fora da área permitida");
      if (!snapshotStat.isDirectory() || snapshotStat.isSymbolicLink())
        throw new Error("Snapshot não pode ser symlink");
      rootReal = await fs.realpath(backupRoot);
      snapshotReal = await fs.realpath(resolvedPath);
    } catch {
      throw new Error("Backup fora da área permitida");
    }
    if (
      !snapshotReal.startsWith(`${rootReal}${path.sep}`) ||
      snapshotReal === rootReal
    )
      throw new Error("Backup fora da área permitida");
  }
  const manifest = JSON.parse(
    await fs.readFile(path.join(resolvedPath, MANIFEST), "utf8"),
  ) as UpdateBackupEntry;
  if (
    manifest.id !== entry.id ||
    manifest.sha256 !== digestTree(manifest.files ?? []) ||
    !manifest.files?.length
  )
    throw new Error("Backup inválido");
  for (const file of manifest.files) {
    const source = path.join(resolvedPath, file.path);
    const relative = path.relative(resolvedPath, source);
    if (
      !relative ||
      relative.startsWith(`..${path.sep}`) ||
      path.isAbsolute(relative) ||
      isExcluded(relative)
    )
      throw new Error("Backup contém caminho inválido");
    const stat = await fs.lstat(source);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size !== file.bytes)
      throw new Error("Backup contém arquivo inválido");
    if (
      createHash("sha256")
        .update(await fs.readFile(source))
        .digest("hex") !== file.sha256
    )
      throw new Error("Integridade do backup inválida");
  }
  const restoreRoot = `${userData}.restore-${Date.now()}`;
  const rollback = `${userData}.rollback-${Date.now()}`;
  await fs.rm(restoreRoot, { recursive: true, force: true });
  await fs.mkdir(restoreRoot, { recursive: true });
  for (const file of manifest.files) {
    const destination = path.join(restoreRoot, file.path);
    await fs.mkdir(path.dirname(destination), { recursive: true });
    await fs.copyFile(path.join(resolvedPath, file.path), destination);
  }
  await fs.cp(userData, rollback, { recursive: true, errorOnExist: false });
  try {
    const backupDirectory = path.basename(path.resolve(backupRoot ?? ""));
    for (const name of await fs.readdir(userData)) {
      if (backupRoot && name === backupDirectory) continue;
      await fs.rm(path.join(userData, name), { recursive: true, force: true });
    }
    for (const file of manifest.files) {
      const destination = path.join(userData, file.path);
      await fs.mkdir(path.dirname(destination), { recursive: true });
      await fs.copyFile(path.join(restoreRoot, file.path), destination);
    }
  } catch (error) {
    await fs.rm(userData, { recursive: true, force: true });
    await fs.cp(rollback, userData, { recursive: true });
    throw error;
  } finally {
    await fs
      .rm(restoreRoot, { recursive: true, force: true })
      .catch(() => undefined);
    await fs
      .rm(rollback, { recursive: true, force: true })
      .catch(() => undefined);
  }
}

/** Serializa restaurações com snapshots para manter `userData` consistente. */
export function restoreUpdateBackup(
  entry: UpdateBackupEntry,
  userData: string,
  backupRoot?: string,
): Promise<void> {
  return withSnapshotLock(() =>
    restoreUpdateBackupUnlocked(entry, userData, backupRoot),
  );
}
