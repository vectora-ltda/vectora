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
