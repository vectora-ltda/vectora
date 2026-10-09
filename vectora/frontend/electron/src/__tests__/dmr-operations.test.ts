import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DmrOperations } from "../dmr-operations.js";
import type { DockerCliExecutor } from "../docker-cli-executor.js";

const executors: Array<{ dispose: () => void }> = [];

async function waitFor(
  predicate: () => boolean,
  timeoutMs = 1000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate() && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  expect(predicate()).toBe(true);
}

afterEach(() => {
  for (const executor of executors.splice(0)) executor.dispose();
});

describe("Electron DMR operation lifecycle", () => {
  it("returns a pollable operation before the Docker command completes", async () => {
    const release = vi.fn<() => void>();
    let resolvePull: (() => void) | undefined;
    const pull = new Promise<void>((resolve) => {
      resolvePull = resolve;
    });
    const executor = {
      execute: vi.fn(async (operation: string) => {
        if (operation === "pull") await pull;
        return {
          operationId: "operation",
          code: 0,
          signal: null,
          stdout: "ok",
          stderr: "",
        };
      }),
      cancel: vi.fn(() => false),
      dispose: release,
    };
    executors.push(executor);
    const directory = await mkdtemp(join(tmpdir(), "vectora-dmr-"));
    const operations = new DmrOperations(
      executor as unknown as DockerCliExecutor,
      directory,
    );

    const initial = await operations.prepare("hf.co/Qwen/Qwen3-0.6B");
    expect(["queued", "running"]).toContain(initial.status);
    expect(operations.getOperation(initial.id)?.status).not.toBe("completed");

    resolvePull?.();
    await waitFor(
      () => operations.getOperation(initial.id)?.status === "completed",
    );
    expect(operations.getOperation(initial.id)?.progress).toBe(100);

    await rm(directory, { recursive: true, force: true });
  });

  it("marks a running operation cancelled and prevents manifest activation", async () => {
    let resolvePull: (() => void) | undefined;
    const pull = new Promise<void>((resolve) => {
      resolvePull = resolve;
    });
    const executor = {
      execute: vi.fn(async () => {
        await pull;
        return {
          operationId: "operation",
          code: 0,
          signal: null,
          stdout: "ok",
          stderr: "",
        };
      }),
      cancel: vi.fn(() => true),
      dispose: vi.fn(),
    };
    executors.push(executor);
    const directory = await mkdtemp(join(tmpdir(), "vectora-dmr-"));
    const operations = new DmrOperations(
      executor as unknown as DockerCliExecutor,
      directory,
    );

    const initial = await operations.prepare("hf.co/Qwen/Qwen3-0.6B");
    await waitFor(
      () => operations.getOperation(initial.id)?.status === "running",
    );
    expect(operations.cancel(initial.id)).toBe(true);
    expect(operations.getOperation(initial.id)?.status).toBe("cancelled");

    resolvePull?.();
    await waitFor(
      () => operations.getOperation(initial.id)?.phase === "cancelled",
    );
    expect((await operations.list()).models).toEqual({});
    await rm(directory, { recursive: true, force: true });
  });
});
