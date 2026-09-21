export interface Workspace {
  readonly roots: readonly string[];
  readFile(uri: string): Promise<Uint8Array>;
  writeFile(uri: string, data: Uint8Array): Promise<void>;
  watch?(uri: string, listener: (event: WorkspaceEvent) => void): Disposable;
}
export interface WorkspaceEvent { uri: string; type: "created" | "changed" | "deleted"; }
export interface Disposable { dispose(): void; }

export class MemoryWorkspace implements Workspace {
  private readonly files = new Map<string, Uint8Array>();
  private readonly watchers = new Map<string, Set<(event: WorkspaceEvent) => void>>();
  constructor(public readonly roots: readonly string[] = ["/"]) {}
  async readFile(uri: string): Promise<Uint8Array> {
    const value = this.files.get(uri);
    if (!value) throw new Error(`file not found: ${uri}`);
    return value.slice();
  }
  async writeFile(uri: string, data: Uint8Array): Promise<void> {
    const type = this.files.has(uri) ? "changed" : "created";
    this.files.set(uri, data.slice());
    this.watchers.get(uri)?.forEach((listener) => listener({ uri, type }));
  }
  watch(uri: string, listener: (event: WorkspaceEvent) => void): Disposable {
    const listeners = this.watchers.get(uri) ?? new Set();
    listeners.add(listener); this.watchers.set(uri, listeners);
    return { dispose: () => { listeners.delete(listener); if (!listeners.size) this.watchers.delete(uri); } };
  }
}
