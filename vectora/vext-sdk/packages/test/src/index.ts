export interface VextTestHost {
  requests: readonly unknown[];
  dispose(): void;
}
export function createTestHost(): VextTestHost {
  return { requests: [], dispose() {} };
}
