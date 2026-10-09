/** Operações de ciclo de vida do Docker Model Runner no processo principal. */

import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { randomUUID } from "node:crypto";
import {
  DockerCliExecutor,
  type DockerCommandResult,
} from "./docker-cli-executor.js";

export type DmrStatus =
  | "ready"
  | "stopped"
  | "docker_unavailable"
  | "plugin_unavailable"
  | "runner_unavailable"
  | "backend_incompatible";

export interface DmrDetection {
  readonly status: DmrStatus;
  readonly docker: boolean;
  readonly plugin: boolean;
  readonly runner: boolean;
  readonly host?: Record<string, unknown>;
  readonly detail?: string;
}

export interface DmrManifestModel {
  readonly reference: string;
  readonly digest?: string;
  readonly engine?: string;
  readonly contextSize?: number;
  readonly source: "docker-model-runner";
  readonly state: "prepared" | "running" | "stopped";
  readonly updatedAt: string;
}

interface DmrManifest {
  version: 1;
  models: Record<string, DmrManifestModel>;
}

export interface DmrOperation {
  readonly id: string;
  readonly operation: "prepare" | "start" | "stop" | "remove";
  readonly reference: string;
  readonly status: "queued" | "running" | "completed" | "failed" | "cancelled";
  readonly phase:
    | "queued"
    | "pulling"
    | "configuring"
    | "starting"
    | "stopping"
    | "removing"
    | "completed"
    | "failed"
    | "cancelled";
  readonly progress: number;
  readonly error?: string;
  readonly output?: string;
}

type MutableOperation = {
  -readonly [K in keyof DmrOperation]: DmrOperation[K];
};

const MAX_TERMINAL_OPERATIONS = 50;

function errorText(result: DockerCommandResult): string {
  return (result.stderr || result.stdout || "Docker Model Runner falhou").slice(
    0,
    2000,
  );
}

function parseJsonObject(value: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

/** Mantém manifestos em userData sem permitir que referências virem caminhos. */
export class DmrOperations {
  private readonly operations = new Map<string, MutableOperation>();
  private readonly tasks = new Map<string, Promise<void>>();
  private manifest: DmrManifest = { version: 1, models: {} };
  private manifestLoaded = false;

  constructor(
    private readonly executor: DockerCliExecutor,
    private readonly userDataPath: string,
  ) {}

  private manifestPath(): string {
    return join(this.userDataPath, "dmr", "manifest.json");
  }

  private async loadManifest(): Promise<void> {
    if (this.manifestLoaded) return;
    this.manifestLoaded = true;
    try {
      const raw: unknown = JSON.parse(
        await readFile(this.manifestPath(), "utf8"),
      );
      if (
        raw &&
        typeof raw === "object" &&
        (raw as { version?: unknown }).version === 1 &&
        (raw as { models?: unknown }).models &&
        typeof (raw as { models: unknown }).models === "object"
      ) {
        this.manifest = raw as DmrManifest;
      }
    } catch {
      // Ausência ou corrupção é tratada como manifesto vazio; a próxima
      // operação grava uma versão válida de forma atômica.
    }
  }

  private async saveManifest(): Promise<void> {
    const path = this.manifestPath();
    const temporary = `${path}.${randomUUID()}.tmp`;
    await mkdir(dirname(path), { recursive: true });
    await writeFile(temporary, JSON.stringify(this.manifest, null, 2), "utf8");
    await rename(temporary, path);
  }

  private createOperation(
    operation: MutableOperation["operation"],
    reference: string,
  ): MutableOperation {
    this.pruneOperations();
    const item: MutableOperation = {
      id: randomUUID(),
      operation,
      reference,
      status: "queued",
      phase: "queued",
      progress: 0,
    };
    this.operations.set(item.id, item);
    return item;
  }

  private pruneOperations(): void {
    const terminal = [...this.operations.values()]
      .filter(
        (operation) =>
          operation.status === "completed" ||
          operation.status === "failed" ||
          operation.status === "cancelled",
      )
      .sort((left, right) => left.id.localeCompare(right.id));
    const excess = terminal.length - MAX_TERMINAL_OPERATIONS;
    for (const operation of excess > 0 ? terminal.slice(0, excess) : []) {
      this.operations.delete(operation.id);
    }
  }

  private async execute(
    item: MutableOperation,
    phase: MutableOperation["phase"],
    progress: number,
    command: () => Promise<DockerCommandResult>,
  ): Promise<DockerCommandResult> {
    if (this.isCancelled(item)) {
      throw new Error("operação Docker cancelada");
    }
    item.status = "running";
    item.phase = phase;
    item.progress = progress;
    try {
      const result = await command();
      item.output = result.stdout.slice(-64 * 1024);
      if (result.code !== 0) throw new Error(errorText(result));
      return result;
    } catch (error) {
      if (!this.isCancelled(item)) {
        item.status = "failed";
        item.phase = "failed";
        item.error = error instanceof Error ? error.message : String(error);
      }
      throw error;
    }
  }

  async detect(): Promise<DmrDetection> {
    let version: DockerCommandResult;
    try {
      version = await this.executor.execute("version", undefined, undefined, {
        timeoutMs: 10_000,
      });
    } catch (error) {
      return {
        status: "docker_unavailable",
        docker: false,
        plugin: false,
        runner: false,
        detail: error instanceof Error ? error.message : String(error),
      };
    }
    if (version.code !== 0) {
      return {
        status: "plugin_unavailable",
        docker: true,
        plugin: false,
        runner: false,
        detail: errorText(version),
      };
    }
    try {
      const info = await this.executor.execute("info");
      if (info.code !== 0) {
        return {
          status: "runner_unavailable",
          docker: true,
          plugin: true,
          runner: false,
          detail: errorText(info),
        };
      }
      return {
        status: "ready",
        docker: true,
        plugin: true,
        runner: true,
        host: parseJsonObject(info.stdout),
      };
    } catch (error) {
      return {
        status: "runner_unavailable",
        docker: true,
        plugin: true,
        runner: false,
        detail: error instanceof Error ? error.message : String(error),
      };
    }
  }

  private queue(
    item: MutableOperation,
    task: () => Promise<void>,
  ): DmrOperation {
    const pending = new Promise<void>((resolve) => setTimeout(resolve, 0)).then(
      task,
    );
    this.tasks.set(item.id, pending);
    void pending.finally(() => this.tasks.delete(item.id));
    return { ...item };
  }

  async prepare(reference: string): Promise<DmrOperation> {
    await this.loadManifest();
    const item = this.createOperation("prepare", reference);
    return this.queue(item, () => this.runPrepare(item));
  }

  async start(reference: string, contextSize?: number): Promise<DmrOperation> {
    await this.loadManifest();
    const item = this.createOperation("start", reference);
    return this.queue(item, () => this.runStart(item, contextSize));
  }

  async stop(reference: string): Promise<DmrOperation> {
    await this.loadManifest();
    const item = this.createOperation("stop", reference);
    return this.queue(item, () => this.runStop(item));
  }

  async remove(reference: string, confirmed: boolean): Promise<DmrOperation> {
    if (!confirmed)
      throw new Error("remoção do modelo exige confirmação explícita");
    await this.loadManifest();
    const item = this.createOperation("remove", reference);
    return this.queue(item, () => this.runRemove(item));
  }

  private async runPrepare(item: MutableOperation): Promise<void> {
    try {
      await this.execute(item, "pulling", 25, () =>
        this.executor.execute("pull", item.reference, undefined, {
          operationId: item.id,
        }),
      );
      if (this.isCancelled(item)) return;
      this.manifest.models[item.reference] = {
        ...(this.manifest.models[item.reference] ?? {}),
        reference: item.reference,
        source: "docker-model-runner",
        state: "prepared",
        updatedAt: new Date().toISOString(),
      };
      await this.saveManifest();
      this.complete(item);
    } catch {
      // `execute` records a structured failure; cancellation is terminal too.
    }
  }

  private async runStart(
    item: MutableOperation,
    contextSize?: number,
  ): Promise<void> {
    try {
      await this.execute(item, "pulling", 20, () =>
        this.executor.execute("pull", item.reference, undefined, {
          operationId: item.id,
        }),
      );
      if (this.isCancelled(item)) return;
      if (contextSize !== undefined) {
        await this.execute(item, "configuring", 50, () =>
          this.executor.execute("configure", item.reference, contextSize, {
            operationId: item.id,
          }),
        );
      }
      if (this.isCancelled(item)) return;
      await this.execute(item, "starting", 75, () =>
        this.executor.execute("run", item.reference, undefined, {
          operationId: item.id,
        }),
      );
      if (this.isCancelled(item)) return;
      this.manifest.models[item.reference] = {
        ...(this.manifest.models[item.reference] ?? {}),
        reference: item.reference,
        contextSize,
        source: "docker-model-runner",
        state: "running",
        updatedAt: new Date().toISOString(),
      };
      await this.saveManifest();
      this.complete(item);
    } catch {
      // `execute` records a structured failure; cancellation is terminal too.
    }
  }

  private async runStop(item: MutableOperation): Promise<void> {
    try {
      await this.execute(item, "stopping", 50, () =>
        this.executor.execute("stop", item.reference, undefined, {
          operationId: item.id,
        }),
      );
      if (this.isCancelled(item)) return;
      const model = this.manifest.models[item.reference];
      if (model) {
        this.manifest.models[item.reference] = {
          ...model,
          state: "stopped",
          updatedAt: new Date().toISOString(),
        };
        await this.saveManifest();
      }
      this.complete(item);
    } catch {
      // `execute` records a structured failure; cancellation is terminal too.
    }
  }

  private async runRemove(item: MutableOperation): Promise<void> {
    try {
      await this.execute(item, "removing", 50, () =>
        this.executor.execute("remove", item.reference, undefined, {
          operationId: item.id,
        }),
      );
      if (this.isCancelled(item)) return;
      delete this.manifest.models[item.reference];
      await this.saveManifest();
      this.complete(item);
    } catch {
      // `execute` records a structured failure; cancellation is terminal too.
    }
  }

  private complete(item: MutableOperation): void {
    item.status = "completed";
    item.phase = "completed";
    item.progress = 100;
  }

  private isCancelled(item: MutableOperation): boolean {
    return item.status === "cancelled";
  }

  async list(): Promise<DmrManifest> {
    await this.loadManifest();
    return structuredClone(this.manifest);
  }

  getOperation(id: string): DmrOperation | undefined {
    const operation = this.operations.get(id);
    return operation ? { ...operation } : undefined;
  }

  listOperations(): DmrOperation[] {
    this.pruneOperations();
    return [...this.operations.values()].map((operation) => ({ ...operation }));
  }

  cancel(id: string): boolean {
    const operation = this.operations.get(id);
    if (
      !operation ||
      (operation.status !== "queued" && operation.status !== "running")
    ) {
      return false;
    }
    operation.status = "cancelled";
    operation.phase = "cancelled";
    this.executor.cancel(id);
    return true;
  }

  dispose(): void {
    this.tasks.clear();
    this.executor.dispose();
  }
}
