export interface Workspace {
  readonly roots: readonly string[];
  readFile(uri: string): Promise<Uint8Array>;
  writeFile(uri: string, data: Uint8Array): Promise<void>;
}
