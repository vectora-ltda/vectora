/**
 * Executor restrito para o Docker Model Runner.
 *
 * O renderer nunca recebe acesso ao shell. Cada operação passa por uma lista
 * de argumentos validada e por `spawn(..., { shell: false })`; a saída e os
 * processos ativos têm limites explícitos para evitar vazamentos de recursos.
 */

import { spawn, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";

export const MAX_OUTPUT_BYTES = 64 * 1024;
export const DEFAULT_TIMEOUT_MS = 120_000;

const MODEL_REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,255}$/;

export type DockerOperation =
  | "version"
  | "info"
  | "list"
  | "inspect"
  | "pull"
  | "run"
  | "configure"
  | "stop"
  | "remove";

export interface DockerCommandResult {
  readonly operationId: string;
  readonly code: number | null;
  readonly signal: NodeJS.Signals | null;
  readonly stdout: string;
  readonly stderr: string;
}

export interface DockerCommandOptions {
  readonly operationId?: string;
  readonly timeoutMs?: number;
  readonly signal?: AbortSignal;
}

export function validateModelReference(reference: string): string {
  const value = reference.trim();
  if (!MODEL_REFERENCE.test(value) || value.startsWith("-")) {
    throw new Error("referência de modelo Docker inválida");
  }
  return value;
}

export function validateContextSize(value: number): number {
  if (!Number.isInteger(value) || value < 1 || value > 1_000_000) {
    throw new Error("tamanho de contexto inválido");
  }
  return value;
}

export function buildDockerArguments(
  operation: DockerOperation,
  reference?: string,
  contextSize?: number,
): string[] {
  switch (operation) {
    case "version":
      return ["model", "version"];
    case "info":
      return ["info", "--format", "{{json .}}"];
    case "list":
      return ["model", "list", "--format", "json"];
    case "inspect":
      return ["model", "inspect", validateModelReference(reference ?? "")];
    case "pull":
      return ["model", "pull", validateModelReference(reference ?? "")];
    case "run":
      return [
        "model",
        "run",
        "--detach",
        validateModelReference(reference ?? ""),
      ];
    case "configure":
      return [
        "model",
        "configure",
        "--context-size",
        String(validateContextSize(contextSize ?? 0)),
        validateModelReference(reference ?? ""),
      ];
    case "stop":
      return ["model", "stop", validateModelReference(reference ?? "")];
    case "remove":
      return ["model", "rm", validateModelReference(reference ?? "")];
  }
}

function appendLimited(
  current: Buffer<ArrayBufferLike>,
  chunk: Buffer<ArrayBufferLike>,
): Buffer<ArrayBufferLike> {
  if (current.length >= MAX_OUTPUT_BYTES) return current;
  return Buffer.concat([current, chunk]).subarray(0, MAX_OUTPUT_BYTES);
}

/** Executa somente operações DMR permitidas e rastreia cancelamento por ID. */
export class DockerCliExecutor {
  private readonly active = new Map<string, ChildProcess>();

  async execute(
    operation: DockerOperation,
    reference?: string,
    contextSize?: number,
    options: DockerCommandOptions = {},
  ): Promise<DockerCommandResult> {
    const operationId = options.operationId ?? randomUUID();
    const args = buildDockerArguments(operation, reference, contextSize);
    const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

    return await new Promise<DockerCommandResult>((resolve, reject) => {
      const child = spawn("docker", args, {
        shell: false,
        windowsHide: true,
        env: {
          PATH: process.env.PATH,
          HOME: process.env.HOME,
          USERPROFILE: process.env.USERPROFILE,
          DOCKER_HOST: process.env.DOCKER_HOST,
        },
        stdio: ["ignore", "pipe", "pipe"],
      });
      this.active.set(operationId, child);
      let stdout: Buffer<ArrayBufferLike> = Buffer.alloc(0);
      let stderr: Buffer<ArrayBufferLike> = Buffer.alloc(0);
      let settled = false;
      const finish = (callback: () => void): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        options.signal?.removeEventListener("abort", abort);
        this.active.delete(operationId);
        callback();
      };
      const abort = (): void => {
        child.kill();
        finish(() => reject(new Error("operação Docker cancelada")));
      };
      const timer = setTimeout(() => {
        child.kill();
        finish(() => reject(new Error("operação Docker excedeu o timeout")));
      }, timeoutMs);

      child.stdout?.on("data", (chunk: Buffer) => {
        stdout = appendLimited(stdout, chunk);
      });
      child.stderr?.on("data", (chunk: Buffer) => {
        stderr = appendLimited(stderr, chunk);
      });
      child.once("error", (error) => finish(() => reject(error)));
      child.once("close", (code, signal) =>
        finish(() =>
          resolve({
            operationId,
            code,
            signal,
            stdout: stdout.toString("utf8"),
            stderr: stderr.toString("utf8"),
          }),
        ),
      );
      if (options.signal?.aborted) abort();
      else options.signal?.addEventListener("abort", abort, { once: true });
    });
  }

  cancel(operationId: string): boolean {
    return this.active.get(operationId)?.kill() ?? false;
  }

  dispose(): void {
    for (const child of this.active.values()) child.kill();
    this.active.clear();
  }
}
