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

  private async execute(
    item: MutableOperation,
    phase: MutableOperation["phase"],
    progress: number,
    command: () => Promise<DockerCommandResult>,
  ): Promise<DockerCommandResult> {
    item.status = "running";
    item.phase = phase;
    item.progress = progress;
    try {
      const result = await command();
      item.output = result.stdout.slice(-64 * 1024);
      if (result.code !== 0) throw new Error(errorText(result));
      return result;
    } catch (error) {
      item.status = "failed";
      item.phase = "failed";
      item.error = error instanceof Error ? error.message : String(error);
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

  async prepare(reference: string): Promise<DmrOperation> {
    await this.loadManifest();
    const item = this.createOperation("prepare", reference);
    await this.execute(item, "pulling", 25, () =>
      this.executor.execute("pull", reference, undefined, {
        operationId: item.id,
      }),
    );
    this.manifest.models[reference] = {
      ...(this.manifest.models[reference] ?? {}),
      reference,
      source: "docker-model-runner",
      state: "prepared",
      updatedAt: new Date().toISOString(),
    };
    await this.saveManifest();
    item.status = "completed";
    item.phase = "completed";
    item.progress = 100;
    return item;
  }

  async start(reference: string, contextSize?: number): Promise<DmrOperation> {
    await this.loadManifest();
    const item = this.createOperation("start", reference);
    await this.execute(item, "pulling", 20, () =>
      this.executor.execute("pull", reference, undefined, {
        operationId: item.id,
      }),
    );
    if (contextSize !== undefined) {
      await this.execute(item, "configuring", 50, () =>
        this.executor.execute("configure", reference, contextSize, {
          operationId: item.id,
        }),
      );
    }
    await this.execute(item, "starting", 75, () =>
      this.executor.execute("run", reference, undefined, {
        operationId: item.id,
      }),
    );
    this.manifest.models[reference] = {
      ...(this.manifest.models[reference] ?? {}),
      reference,
      contextSize,
      source: "docker-model-runner",
      state: "running",
      updatedAt: new Date().toISOString(),
    };
    await this.saveManifest();
    item.status = "completed";
    item.phase = "completed";
    item.progress = 100;
    return item;
  }

  async stop(reference: string): Promise<DmrOperation> {
    await this.loadManifest();
    const item = this.createOperation("stop", reference);
    await this.execute(item, "stopping", 50, () =>
      this.executor.execute("stop", reference, undefined, {
        operationId: item.id,
      }),
    );
    const model = this.manifest.models[reference];
    if (model) {
      this.manifest.models[reference] = {
        ...model,
        state: "stopped",
        updatedAt: new Date().toISOString(),
      };
      await this.saveManifest();
    }
    item.status = "completed";
    item.phase = "completed";
    item.progress = 100;
    return item;
  }

  async remove(reference: string, confirmed: boolean): Promise<DmrOperation> {
    if (!confirmed)
      throw new Error("remoção do modelo exige confirmação explícita");
    await this.loadManifest();
    const item = this.createOperation("remove", reference);
    await this.execute(item, "removing", 50, () =>
      this.executor.execute("remove", reference, undefined, {
        operationId: item.id,
      }),
    );
    delete this.manifest.models[reference];
    await this.saveManifest();
    item.status = "completed";
    item.phase = "completed";
    item.progress = 100;
    return item;
  }

  async list(): Promise<DmrManifest> {
    await this.loadManifest();
    return structuredClone(this.manifest);
  }

  getOperation(id: string): DmrOperation | undefined {
    const operation = this.operations.get(id);
    return operation ? { ...operation } : undefined;
  }

  cancel(id: string): boolean {
    const operation = this.operations.get(id);
    if (!operation || operation.status !== "running") return false;
    operation.status = "cancelled";
    operation.phase = "cancelled";
    return this.executor.cancel(id);
  }

  dispose(): void {
    this.executor.dispose();
  }
}
