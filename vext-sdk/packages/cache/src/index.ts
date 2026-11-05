export interface Cache {
  get<T>(key: string): Promise<T | undefined>;
  set<T>(key: string, value: T, ttlMs?: number): Promise<void>;
  delete(key: string): Promise<void>;
  clearNamespace(namespace: string): Promise<void>;
  lock?<T>(key: string, work: () => Promise<T>): Promise<T>;
}

export class MemoryCache implements Cache {
  private readonly entries = new Map<
    string,
    { value: unknown; expiresAt?: number }
  >();
  private readonly locks = new Map<string, Promise<void>>();
  constructor(private readonly namespace = "default") {}
  private key(key: string): string {
    return `${this.namespace}:${key}`;
  }
  async get<T>(key: string): Promise<T | undefined> {
    const entry = this.entries.get(this.key(key));
    if (!entry) return undefined;
    if (entry.expiresAt !== undefined && entry.expiresAt <= Date.now()) {
      this.entries.delete(this.key(key));
      return undefined;
    }
    return entry.value as T;
  }
  async set<T>(key: string, value: T, ttlMs?: number): Promise<void> {
    this.entries.set(this.key(key), {
      value,
      expiresAt: ttlMs === undefined ? undefined : Date.now() + ttlMs,
    });
  }
  async delete(key: string): Promise<void> {
    this.entries.delete(this.key(key));
  }
  async clearNamespace(namespace: string): Promise<void> {
    for (const key of this.entries.keys())
      if (key.startsWith(`${namespace}:`)) this.entries.delete(key);
  }
  async lock<T>(key: string, work: () => Promise<T>): Promise<T> {
    const previous = this.locks.get(key) ?? Promise.resolve();
    let release!: () => void;
    const current = new Promise<void>((resolve) => { release = resolve; });
    const queued = previous.then(() => current);
    this.locks.set(key, queued);
    await previous;
    try { return await work(); } finally { release(); if (this.locks.get(key) === queued) this.locks.delete(key); }
  }
}
