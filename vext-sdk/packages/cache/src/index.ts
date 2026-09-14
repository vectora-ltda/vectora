export interface Cache {
  get<T>(key: string): Promise<T | undefined>;
  set<T>(key: string, value: T, ttlMs?: number): Promise<void>;
  delete(key: string): Promise<void>;
  clearNamespace(namespace: string): Promise<void>;
}

export class MemoryCache implements Cache {
  private readonly entries = new Map<
    string,
    { value: unknown; expiresAt?: number }
  >();
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
}
