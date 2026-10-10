import { promises as fs } from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { isValidProfileId } from "./browser-ipc-validation.js";

export interface BrowserCredentialRecord {
  id: string;
  origin: string;
  username: string;
  password: string;
  createdAt: string;
  updatedAt: string;
}

interface Encryption {
  isEncryptionAvailable(): boolean;
  encryptString(value: string): Buffer;
  decryptString(value: Buffer): string;
}

/** Owns serialized credential transactions and atomic encrypted persistence per profile. */
export class BrowserCredentialStore {
  private queues = new Map<string, Promise<unknown>>();
  constructor(
    private root: string,
    private encryption: Encryption,
  ) {}

  /** Reject untrusted identifiers before resolving any filesystem path. */
  private file(profile: string): string {
    if (!isValidProfileId(profile)) throw new Error("perfil inválido");
    return path.join(this.root, `${profile}.dat`);
  }

  /** Keep failures local to one transaction without poisoning subsequent operations. */
  private transact<T>(
    profile: string,
    operation: () => Promise<T>,
  ): Promise<T> {
    this.file(profile);
    const next = (this.queues.get(profile) ?? Promise.resolve())
      .catch(() => undefined)
      .then(operation);
    this.queues.set(profile, next);
    void next
      .finally(() => {
        if (this.queues.get(profile) === next) this.queues.delete(profile);
      })
      .catch(() => undefined);
    return next;
  }

  /** Only a missing file represents an empty vault; corruption must remain visible. */
  private async read(profile: string): Promise<BrowserCredentialRecord[]> {
    let raw: Buffer;
    try {
      raw = await fs.readFile(this.file(profile));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
    if (!this.encryption.isEncryptionAvailable())
      throw new Error("armazenamento seguro indisponível");
    const records: unknown = JSON.parse(this.encryption.decryptString(raw));
    if (
      !Array.isArray(records) ||
      !records.every((record: unknown) => {
        if (!record || typeof record !== "object") return false;
        return [
          "id",
          "origin",
          "username",
          "password",
          "createdAt",
          "updatedAt",
        ].every(
          (key) => typeof (record as Record<string, unknown>)[key] === "string",
        );
      })
    )
      throw new Error("arquivo de credenciais inválido");
    return records as BrowserCredentialRecord[];
  }

  /** Readers wait for outstanding mutations of the same profile. */
  list(profile: string): Promise<BrowserCredentialRecord[]> {
    return this.transact(profile, () => this.read(profile));
  }

  /** Replace the encrypted file only after its complete temporary copy is durable. */
  private async write(
    profile: string,
    records: BrowserCredentialRecord[],
  ): Promise<void> {
    if (!this.encryption.isEncryptionAvailable())
      throw new Error("armazenamento seguro indisponível");
    const file = this.file(profile);
    await fs.mkdir(this.root, { recursive: true });
    const temporary = `${file}.${randomUUID()}.tmp`;
    try {
      const handle = await fs.open(temporary, "wx", 0o600);
      try {
        await handle.writeFile(
          this.encryption.encryptString(JSON.stringify(records)),
        );
        await handle.sync();
      } finally {
        await handle.close();
      }
      await fs.rename(temporary, file);
    } finally {
      await fs.rm(temporary, { force: true });
    }
  }

  /** Apply read/modify/write as one transaction so deletion cannot be resurrected. */
  save(
    profile: string,
    input: Pick<BrowserCredentialRecord, "origin" | "username" | "password">,
  ): Promise<BrowserCredentialRecord> {
    return this.transact(profile, async () => {
      const records = await this.read(profile);
      const existing = records.find(
        (record) =>
          record.origin === input.origin && record.username === input.username,
      );
      const now = new Date().toISOString();
      const record = {
        ...input,
        id: existing?.id ?? randomUUID(),
        createdAt: existing?.createdAt ?? now,
        updatedAt: now,
      };
      await this.write(profile, [
        ...records.filter((item) => item.id !== record.id),
        record,
      ]);
      return record;
    });
  }

  /** Delete within the same queue as save and profile cleanup. */
  delete(profile: string, id: string): Promise<void> {
    return this.transact(profile, async () => {
      const records = (await this.read(profile)).filter(
        (record) => record.id !== id,
      );
      if (!records.length) await fs.rm(this.file(profile), { force: true });
      else await this.write(profile, records);
    });
  }

  /** Cleanup requires no decryption support, including for the default profile. */
  clear(profile: string): Promise<void> {
    return this.transact(profile, () =>
      fs.rm(this.file(profile), { force: true }),
    );
  }
}
