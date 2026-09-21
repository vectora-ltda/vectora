export interface VextTestHost {
  requests: readonly unknown[];
  invoke(request: unknown): Promise<unknown>;
  dispose(): void;
}
export function createTestHost(): VextTestHost {
  const requests: unknown[] = [];
  let disposed = false;
  return {
    requests,
    async invoke(request: unknown): Promise<unknown> {
      if (disposed) throw new Error("VEXT test host is disposed");
      requests.push(request);
      return undefined;
    },
    dispose() {
      disposed = true;
    },
  };
}

export function assertRequestCount(host: VextTestHost, expected: number): void {
  if (host.requests.length !== expected)
    throw new Error(
      `Expected ${expected} requests, received ${host.requests.length}`,
    );
}
