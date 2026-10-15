import { mkdtempSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  buildDockerArguments,
  resolveDockerExecutable,
  validateContextSize,
  validateModelReference,
} from "../docker-cli-executor.js";

const previousDockerCliPath = process.env.DOCKER_CLI_PATH;

afterEach(() => {
  if (previousDockerCliPath === undefined) delete process.env.DOCKER_CLI_PATH;
  else process.env.DOCKER_CLI_PATH = previousDockerCliPath;
});

describe("Docker Model Runner command contract", () => {
  it("gera somente argumentos do allowlist para pull e start", () => {
    expect(buildDockerArguments("pull", "hf.co/Qwen/Qwen3-0.6B")).toEqual([
      "model",
      "pull",
      "hf.co/Qwen/Qwen3-0.6B",
    ]);
    expect(buildDockerArguments("run", "hf.co/Qwen/Qwen3-0.6B")).toEqual([
      "model",
      "run",
      "--detach",
      "hf.co/Qwen/Qwen3-0.6B",
    ]);
  });

  it("rejeita referências que tentariam inserir flags ou shell", () => {
    expect(() => validateModelReference("--help")).toThrow();
    expect(() => validateModelReference("hf.co/model;rm -rf /")).toThrow();
    expect(() => validateModelReference("hf.co/model\u0000x")).toThrow();
  });

  it("limita o contexto e rejeita valores não inteiros", () => {
    expect(validateContextSize(4096)).toBe(4096);
    expect(() => validateContextSize(0)).toThrow();
    expect(() => validateContextSize(1.5)).toThrow();
    expect(() => validateContextSize(1_000_001)).toThrow();
  });

  it("configuração inclui contexto validado como argumento separado", () => {
    expect(
      buildDockerArguments("configure", "hf.co/Qwen/Qwen3-0.6B", 4096),
    ).toEqual([
      "model",
      "configure",
      "--context-size",
      "4096",
      "hf.co/Qwen/Qwen3-0.6B",
    ]);
  });

  it("resolve o CLI por caminho absoluto configurado, sem consultar PATH", () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "vectora-docker-"));
    const executable = path.join(dir, "docker.exe");
    writeFileSync(executable, "");
    expect(
      resolveDockerExecutable({ DOCKER_CLI_PATH: executable, PATH: "" }),
    ).toBe(path.resolve(executable));
  });

  it("rejeita configuração relativa em vez de executar pelo diretório atual", () => {
    expect(() =>
      resolveDockerExecutable({ DOCKER_CLI_PATH: "docker.exe" }),
    ).toThrow(/caminho absoluto/);
  });
});
