export interface ProcessLimits {
  timeoutMs?: number;
  maxOutputBytes?: number;
}
export interface ProcessResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
}
export interface ProcessRunner {
  run(
    command: string,
    args: readonly string[],
    limits?: ProcessLimits,
  ): Promise<ProcessResult>;
}

export class ProcessTimeoutError extends Error {
  constructor(message = "VEXT process timed out") {
    super(message);
    this.name = "ProcessTimeoutError";
  }
}

/** Wraps any runner with a deterministic timeout without assuming a host runtime. */
export function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  onTimeout?: () => void,
): Promise<T> {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) return promise;
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      onTimeout?.();
      reject(new ProcessTimeoutError());
    }, timeoutMs);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

export function validateProcessResult(
  result: ProcessResult,
  limits: ProcessLimits = {},
): ProcessResult {
  const max = limits.maxOutputBytes;
  if (
    max !== undefined &&
    (new TextEncoder().encode(result.stdout).byteLength > max ||
      new TextEncoder().encode(result.stderr).byteLength > max)
  ) {
    throw new Error("VEXT process output exceeded the configured limit");
  }
  return result;
}
