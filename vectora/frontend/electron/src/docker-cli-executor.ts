/**
 * Executor restrito para o Docker Model Runner.
 *
 * O renderer nunca recebe acesso ao shell. Cada operação passa por uma lista
 * de argumentos validada e por `spawn(..., { shell: false })`; a saída e os
 * processos ativos têm limites explícitos para evitar vazamentos de recursos.
 */

import { spawn, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import killTree from "tree-kill";

export const MAX_OUTPUT_BYTES = 64 * 1024;
export const DEFAULT_TIMEOUT_MS = 120_000;

const MODEL_REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,255}$/;

/**
 * Resolve Docker Desktop without relying on the Electron process PATH.
 *
 * GUI-launched Electron processes commonly inherit a stale or reduced PATH.
 * Executing the bare `docker` name would then either hit an unrelated binary
 * or fail even while Docker Desktop is running.  An explicit override is
 * supported for managed installations; otherwise use only documented
 * absolute locations for the current platform.
 */
export function resolveDockerExecutable(
  env: NodeJS.ProcessEnv = process.env,
): string {
  const configured = env.DOCKER_CLI_PATH?.trim();
  const candidates: string[] = [];
  if (configured) {
    if (!path.isAbsolute(configured)) {
      throw new Error("DOCKER_CLI_PATH deve ser um caminho absoluto");
    }
    candidates.push(configured);
  }

  if (process.platform === "win32") {
    const programFiles = env.ProgramW6432 || env.ProgramFiles;
    const localAppData = env.LOCALAPPDATA;
    if (programFiles) {
      candidates.push(
        path.join(
          programFiles,
          "Docker",
          "Docker",
          "resources",
          "bin",
          "docker.exe",
        ),
      );
    }
    if (localAppData) {
      candidates.push(
        path.join(
          localAppData,
          "Programs",
          "DockerDesktop",
          "resources",
          "bin",
          "docker.exe",
        ),
      );
    }
  } else {
    candidates.push("/usr/local/bin/docker", "/usr/bin/docker");
    candidates.push(
      path.join(env.HOME || os.homedir(), ".docker", "bin", "docker"),
    );
  }

  for (const candidate of candidates) {
    if (path.isAbsolute(candidate) && existsSync(candidate)) {
      return path.resolve(candidate);
    }
  }
  throw new Error(
    "Docker CLI não encontrado em um caminho absoluto; configure DOCKER_CLI_PATH",
  );
}

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

function terminateProcessTree(child: ChildProcess): boolean {
  if (!child.pid) return child.kill();
  try {
    killTree(child.pid, "SIGTERM", (error) => {
      if (error) child.kill();
    });
    return true;
  } catch {
    return child.kill();
  }
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
    const executable = resolveDockerExecutable();

    return await new Promise<DockerCommandResult>((resolve, reject) => {
      const child = spawn(executable, args, {
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
        terminateProcessTree(child);
        finish(() => reject(new Error("operação Docker cancelada")));
      };
      const timer = setTimeout(() => {
        terminateProcessTree(child);
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
    const child = this.active.get(operationId);
    return child ? terminateProcessTree(child) : false;
  }

  dispose(): void {
    for (const child of this.active.values()) terminateProcessTree(child);
    this.active.clear();
  }
}
