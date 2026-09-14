export interface Workspace {
  readonly roots: readonly string[];
  readFile(uri: string): Promise<Uint8Array>;
  writeFile(uri: string, data: Uint8Array): Promise<void>;
}

export class MemoryWorkspace implements Workspace {
  private readonly files = new Map<string, Uint8Array>();
  constructor(public readonly roots: readonly string[] = ["/"]) {}
  async readFile(uri: string): Promise<Uint8Array> {
    const value = this.files.get(uri);
    if (!value) throw new Error(`file not found: ${uri}`);
    return value.slice();
  }
  async writeFile(uri: string, data: Uint8Array): Promise<void> {
    this.files.set(uri, data.slice());
  }
}
