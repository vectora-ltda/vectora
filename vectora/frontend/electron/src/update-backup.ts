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
const LOCK_RETRY_ATTEMPTS = 4;
const LOCK_RETRY_DELAY_MS = 150;
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

function isTransientFileLock(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const code = (error as { code?: unknown }).code;
  return code === "EBUSY" || code === "EPERM" || code === "EACCES";
}

export async function withFileLockRetry<T>(
  operation: () => Promise<T>,
): Promise<T> {
  let attempt = 0;
  while (true) {
    try {
      return await operation();
    } catch (error) {
      if (!isTransientFileLock(error) || attempt >= LOCK_RETRY_ATTEMPTS)
        throw error;
      attempt += 1;
      await new Promise<void>((resolve) =>
        setTimeout(resolve, LOCK_RETRY_DELAY_MS * 2 ** (attempt - 1)),
      );
    }
  }
}

function isExcluded(relativePath: string): boolean {
  return relativePath
    .split(path.sep)
    .some((component) => EXCLUDED.has(component));
}

/** Resolve a relative backup path without allowing it to escape its root. */
function resolveBackupPath(root: string, relativePath: string): string {
  const resolvedRoot = path.resolve(root);
  const resolvedPath = path.resolve(resolvedRoot, relativePath);
  if (
    resolvedPath !== resolvedRoot &&
    !resolvedPath.startsWith(`${resolvedRoot}${path.sep}`)
  ) {
    throw new Error("Caminho do backup fora da área permitida");
  }
  return resolvedPath;
}

function digestTree(files: readonly UpdateBackupFile[]): string {
  const hash = createHash("sha256");
  for (const file of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
    hash.update(`${file.path}\0${file.bytes}\0${file.sha256}\n`);
  }
  return hash.digest("hex");
}

/** Coleta arquivos e registra caminhos omitidos por bloqueio transitório. */
async function collectFiles(
  root: string,
  current = root,
  skipped: string[] = [],
): Promise<string[]> {
  const result: string[] = [];
  let names: string[];
  try {
    names = await fs.readdir(current);
  } catch (error) {
    if (!isTransientFileLock(error)) throw error;
    const relative = path.relative(root, current);
    if (!relative) throw error;
    skipped.push(relative);
    console.warn("[updater] backup omitindo diretório bloqueado", {
      path: relative || ".",
      error,
    });
    return result;
  }
  for (const name of names) {
    const relative = path.relative(root, path.join(current, name));
    if (isExcluded(relative) || name.endsWith(".lock")) continue;
    const source = path.join(current, name);
    let stat;
    try {
      stat = await withFileLockRetry(() => fs.lstat(source));
    } catch (error) {
      if (isTransientFileLock(error)) {
        skipped.push(relative);
        console.warn("[updater] backup omitindo caminho bloqueado", {
          path: relative,
          error,
        });
        continue;
      }
      throw error;
    }
    if (stat.isSymbolicLink()) throw new Error("userData contém symlink");
    if (stat.isDirectory()) {
      result.push(...(await collectFiles(root, source, skipped)));
    } else if (stat.isFile()) {
      if (stat.size <= MAX_FILE_BYTES) {
        result.push(relative);
      } else {
        skipped.push(relative);
        console.warn("[updater] backup omitindo arquivo acima do limite", {
          path: relative,
          bytes: stat.size,
          limit: MAX_FILE_BYTES,
        });
      }
    } else {
      skipped.push(relative);
      console.warn("[updater] backup omitindo entrada não regular", {
        path: relative,
      });
    }
  }
  return result;
}

/** Remove managed state while retaining excluded directories at any depth. */
async function removeManagedContent(
  root: string,
  current: string,
  backupDirectory?: string,
): Promise<void> {
  for (const name of await fs.readdir(current)) {
    const relative = path.relative(root, path.join(current, name));
    if (
      (relative === backupDirectory && path.dirname(relative) === ".") ||
      EXCLUDED.has(name)
    )
      continue;
    const target = path.join(current, name);
    const stat = await withFileLockRetry(() => fs.lstat(target));
    if (stat.isDirectory() && !stat.isSymbolicLink()) {
      await removeManagedContent(root, target, backupDirectory);
      // A parent that contains an excluded child must remain; empty parents
      // created only by the previous state can be removed safely.
      await withFileLockRetry(() => fs.rmdir(target)).catch(
        (error: unknown) => {
          const code = (error as { code?: unknown }).code;
          if (code !== "ENOTEMPTY" && code !== "EEXIST") throw error;
        },
      );
      continue;
    }
    await withFileLockRetry(() => fs.rm(target, { force: true }));
  }
}

/** Copy one regular source file and fail closed when the destination is unavailable. */
async function copySafe(
  source: string,
  destination: string,
): Promise<UpdateBackupFile> {
  const stat = await withFileLockRetry(() => fs.lstat(source));
  if (!stat.isFile() || stat.isSymbolicLink())
    throw new Error("entrada não regular");
  if (stat.size > MAX_FILE_BYTES) throw new Error("arquivo excede o limite");
  const data = await withFileLockRetry(() => fs.readFile(source));
  try {
    await withFileLockRetry(() =>
      fs.mkdir(path.dirname(destination), { recursive: true }),
    );
    await withFileLockRetry(() =>
      fs.writeFile(destination, data, { mode: 0o600 }),
    );
  } catch (error) {
    throw new Error("falha ao gravar arquivo no destino do backup", {
      cause: error,
    });
  }
  return {
    path: "",
    bytes: data.byteLength,
    sha256: createHash("sha256").update(data).digest("hex"),
  };
}

/** Serialize snapshot creation and restoration to avoid concurrent mutations. */
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
      const skipped: string[] = [];
      for (const relative of await collectFiles(userData, userData, skipped)) {
        try {
          const copied = await copySafe(
            path.join(userData, relative),
            resolveBackupPath(temporary, relative),
          );
          files.push({ ...copied, path: relative });
        } catch (error) {
          if (!isTransientFileLock(error)) throw error;
          skipped.push(relative);
          console.warn("[updater] backup omitindo arquivo bloqueado", {
            path: relative,
            error,
          });
        }
      }
      const normalizedSkipped = [...new Set(skipped)].sort((a, b) =>
        a.localeCompare(b),
      );
      const manifest: UpdateBackupEntry = {
        id,
        createdAt: new Date().toISOString(),
        appVersion,
        path: destination,
        bytes: files.reduce((sum, file) => sum + file.bytes, 0),
        sha256: digestTree(files),
        files,
        ...(normalizedSkipped.length > 0 ? { skipped: normalizedSkipped } : {}),
      };
      await withFileLockRetry(() =>
        fs.writeFile(
          path.join(temporary, MANIFEST),
          JSON.stringify(manifest, null, 2),
          { mode: 0o600 },
        ),
      );
      await withFileLockRetry(() => fs.rename(temporary, destination));
      const completeEntries: string[] = [];
      for (const entry of await fs.readdir(backupRoot)) {
        if (entry.startsWith(".tmp-")) continue;
        try {
          const manifest = JSON.parse(
            await fs.readFile(path.join(backupRoot, entry, MANIFEST), "utf8"),
          ) as Partial<UpdateBackupEntry>;
          if (!manifest.skipped || manifest.skipped.length === 0)
            completeEntries.push(entry);
        } catch {
          // Ignore malformed or unrelated directories during retention.
        }
      }
      completeEntries.sort().reverse();
      await Promise.all(
        completeEntries.slice(maxBackups).map((entry) =>
          withFileLockRetry(() =>
            fs.rm(path.join(backupRoot, entry), {
              recursive: true,
              force: true,
            }),
          ).catch(() => undefined),
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
      if (
        Array.isArray(entry.files) &&
        digestTree(entry.files) === entry.sha256
      )
        entries.push(entry);
    } catch {
      /* diretório temporário ou snapshot incompleto */
    }
  }
  return entries.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

/** Validate and restore a complete update snapshot while preserving managed files. */
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
    !Array.isArray(manifest.files) ||
    manifest.sha256 !== digestTree(manifest.files)
  )
    throw new Error("Backup inválido");
  if (manifest.skipped && manifest.skipped.length > 0)
    throw new Error("Backup parcial não pode ser restaurado");
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
  await withFileLockRetry(() =>
    fs.rm(restoreRoot, { recursive: true, force: true }),
  );
  await withFileLockRetry(() => fs.mkdir(restoreRoot, { recursive: true }));
  for (const file of manifest.files) {
    const destination = path.join(restoreRoot, file.path);
    await withFileLockRetry(() =>
      fs.mkdir(path.dirname(destination), { recursive: true }),
    );
    await withFileLockRetry(() =>
      fs.copyFile(path.join(resolvedPath, file.path), destination),
    );
  }
  await withFileLockRetry(() =>
    fs.cp(userData, rollback, { recursive: true, errorOnExist: false }),
  );
  try {
    const backupDirectory = path.basename(path.resolve(backupRoot ?? ""));
    await removeManagedContent(
      userData,
      userData,
      backupRoot ? backupDirectory : undefined,
    );
    for (const file of manifest.files) {
      const destination = path.join(userData, file.path);
      await withFileLockRetry(() =>
        fs.mkdir(path.dirname(destination), { recursive: true }),
      );
      await withFileLockRetry(() =>
        fs.copyFile(path.join(restoreRoot, file.path), destination),
      );
    }
  } catch (error) {
    await withFileLockRetry(() =>
      fs.rm(userData, { recursive: true, force: true }),
    );
    await withFileLockRetry(() =>
      fs.cp(rollback, userData, { recursive: true }),
    );
    throw error;
  } finally {
    await withFileLockRetry(() =>
      fs.rm(restoreRoot, { recursive: true, force: true }),
    ).catch(() => undefined);
    await withFileLockRetry(() =>
      fs.rm(rollback, { recursive: true, force: true }),
    ).catch(() => undefined);
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
