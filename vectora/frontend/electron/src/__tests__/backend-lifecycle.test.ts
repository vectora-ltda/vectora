import { describe, it, expect, afterEach } from "vitest";
import { spawn, ChildProcess } from "child_process";
import * as path from "path";
import treeKill from "tree-kill";
import {
  getFreePort,
  IpcPipeParser,
  parseIpcPipeFromText,
  pingBackendHttp,
  waitForBackendReady,
  killBackendTree,
  backendPath,
  isInstalledTestsMode,
  natsBinaryPath,
  resolveExternalBackendConnection,
} from "../backend-lifecycle.js";

// .cjs (não .js): frontend/package.json declara "type": "module" — sem a
// extensão explícita, Node tentaria parsear este require() puro como ESM.
const DUMMY_BACKEND = path.join(__dirname, "fixtures", "dummy-backend.cjs");

function unreachableExistsFn(): never {
  throw new Error("não deveria ser chamado com override setado");
}

let spawned: ChildProcess[] = [];

function spawnDummy(env: NodeJS.ProcessEnv): ChildProcess {
  const child = spawn(process.execPath, [DUMMY_BACKEND], {
    env: { ...process.env, ...env },
    stdio: ["ignore", "pipe", "pipe"],
  });
  spawned.push(child);
  return child;
}

afterEach(async () => {
  await Promise.all(
    spawned
      .filter((child) => child.pid && child.exitCode === null)
      .map(
        (child) =>
          new Promise<void>((resolve) => {
            treeKill(child.pid!, () => resolve());
          }),
      ),
  );
  spawned = [];
});

// ---------------------------------------------------------------------------
// getFreePort
// ---------------------------------------------------------------------------

describe("getFreePort", () => {
  it("retorna uma porta livre de verdade (consegue escutar nela)", async () => {
    const port = await getFreePort();
    expect(port).toBeGreaterThan(0);
  });

  it("chamadas concorrentes nunca retornam a mesma porta", async () => {
    const [a, b, c] = await Promise.all([
      getFreePort(),
      getFreePort(),
      getFreePort(),
    ]);
    expect(new Set([a, b, c]).size).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// backendPath / natsBinaryPath — funções puras
// ---------------------------------------------------------------------------

describe("isInstalledTestsMode", () => {
  it("detecta --tests sem confundir argumentos normais do Electron", () => {
    expect(isInstalledTestsMode(["/app/Vectora", "--tests"])).toBe(true);
    expect(isInstalledTestsMode(["/app/Vectora", "--version"])).toBe(false);
    expect(isInstalledTestsMode(["/app/Vectora", "start"])).toBe(false);
  });
});

describe("backendPath", () => {
  it("usa VECTORA_CORE_PATH quando setado (dev)", () => {
    const p = backendPath(
      { VECTORA_CORE_PATH: "C:\\dev\\build" },
      "win32",
      "C:\\resources",
    );
    expect(p).toBe(path.join("C:\\dev\\build", "vectora.exe"));
  });

  it("usa resourcesPath/vectora-core quando sem override (prod)", () => {
    const p = backendPath({}, "linux", "/opt/vectora/resources");
    expect(p).toBe(
      path.join("/opt/vectora/resources", "vectora-core", "vectora"),
    );
  });
});

describe("natsBinaryPath", () => {
  it("usa VECTORA_NATS_BINARY quando setado, sem checar filesystem", () => {
    const p = natsBinaryPath(
      { VECTORA_NATS_BINARY: "/custom/nats-server" },
      "linux",
      "/resources",
      unreachableExistsFn,
    );
    expect(p).toBe("/custom/nats-server");
  });

  it("retorna null quando o binário não existe no resourcesPath", () => {
    const p = natsBinaryPath({}, "linux", "/resources", () => false);
    expect(p).toBeNull();
  });

  it("retorna o path quando o binário existe no resourcesPath", () => {
    const p = natsBinaryPath({}, "win32", "C:\\resources", () => true);
    expect(p).toBe(path.join("C:\\resources", "nats-server.exe"));
  });
});

describe("resolveExternalBackendConnection", () => {
  it("retorna null sem VECTORA_EXTERNAL_BACKEND=1 (caminho normal, produção)", () => {
    expect(resolveExternalBackendConnection({})).toBeNull();
    expect(
      resolveExternalBackendConnection({ VECTORA_PORT: "8080" }),
    ).toBeNull();
  });

  it("resolve porta e pipe quando o backend já é o processo primário", () => {
    const result = resolveExternalBackendConnection({
      VECTORA_EXTERNAL_BACKEND: "1",
      VECTORA_PORT: "8080",
      VECTORA_IPC_PIPE: "\\\\.\\pipe\\vectora-1234",
    });
    expect(result).toEqual({
      port: 8080,
      pipePath: "\\\\.\\pipe\\vectora-1234",
    });
  });

  it("edge case: sem VECTORA_IPC_PIPE (Linux/macOS, usa unix socket fixo), pipePath fica null", () => {
    const result = resolveExternalBackendConnection({
      VECTORA_EXTERNAL_BACKEND: "1",
      VECTORA_PORT: "8080",
    });
    expect(result).toEqual({ port: 8080, pipePath: null });
  });

  it("edge case: VECTORA_PORT ausente ou não-numérico vira null, não NaN", () => {
    expect(
      resolveExternalBackendConnection({ VECTORA_EXTERNAL_BACKEND: "1" })?.port,
    ).toBeNull();
    expect(
      resolveExternalBackendConnection({
        VECTORA_EXTERNAL_BACKEND: "1",
        VECTORA_PORT: "not-a-number",
      })?.port,
    ).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// IpcPipeParser — parsing do handshake VECTORA_IPC_PIPE=
// ---------------------------------------------------------------------------

describe("parseIpcPipeFromText", () => {
  it("extrai o pipe path de uma linha completa", () => {
    expect(
      parseIpcPipeFromText("VECTORA_IPC_PIPE=\\\\.\\pipe\\vectora-123\n"),
    ).toBe("\\\\.\\pipe\\vectora-123");
  });

  it("retorna null quando o marcador não está presente", () => {
    expect(parseIpcPipeFromText("qualquer outro log de boot\n")).toBeNull();
  });
});

describe("IpcPipeParser", () => {
  it("captura o marcador quando chega inteiro num único chunk", () => {
    const parser = new IpcPipeParser();
    const result = parser.push("VECTORA_IPC_PIPE=\\\\.\\pipe\\vectora-1\n");
    expect(result).toBe("\\\\.\\pipe\\vectora-1");
  });

  it("edge case: reagrupa o marcador fatiado entre dois chunks", () => {
    const parser = new IpcPipeParser();
    const first = parser.push("VECTORA_IPC_PIPE=\\\\.\\pipe\\vectora-");
    expect(first).toBeNull(); // linha ainda incompleta — sem \n
    const second = parser.push("999\n");
    expect(second).toBe("\\\\.\\pipe\\vectora-999");
  });

  it("ignora linhas anteriores sem o marcador antes de achar a linha certa", () => {
    const parser = new IpcPipeParser();
    parser.push("log de boot 1\nlog de boot 2\n");
    const result = parser.push("VECTORA_IPC_PIPE=\\\\.\\pipe\\vectora-2\n");
    expect(result).toBe("\\\\.\\pipe\\vectora-2");
  });
});

// ---------------------------------------------------------------------------
// spawn real + IpcPipeParser contra o dummy backend
// ---------------------------------------------------------------------------

describe("spawn real do dummy backend", () => {
  it("emite VECTORA_IPC_PIPE no stdout e o parser captura corretamente", async () => {
    const port = await getFreePort();
    const child = spawnDummy({ TEST_HEALTH_PORT: String(port) });
    const parser = new IpcPipeParser();
    let captured: string | null = null;

    await new Promise<void>((resolve) => {
      child.stdout?.on("data", (b: Buffer) => {
        const found = parser.push(b.toString());
        if (found) {
          captured = found;
          resolve();
        }
      });
    });

    expect(captured).toBe("\\\\.\\pipe\\test-dummy-backend");
  });

  it("edge case: write parcial fatiado em dois eventos 'data' ainda é capturado", async () => {
    const port = await getFreePort();
    const child = spawnDummy({
      TEST_HEALTH_PORT: String(port),
      SPLIT_PIPE_WRITE: "1",
    });
    const parser = new IpcPipeParser();
    let captured: string | null = null;

    await new Promise<void>((resolve) => {
      child.stdout?.on("data", (b: Buffer) => {
        const found = parser.push(b.toString());
        if (found) {
          captured = found;
          resolve();
        }
      });
    });

    expect(captured).toBe("\\\\.\\pipe\\test-dummy-backend");
  });
});

// ---------------------------------------------------------------------------
// pingBackendHttp / waitForBackendReady — contra HTTP real
// ---------------------------------------------------------------------------

describe("waitForBackendReady", () => {
  it("resolve assim que o dummy backend abre /health", async () => {
    const port = await getFreePort();
    spawnDummy({ TEST_HEALTH_PORT: String(port), DELAY_HEALTH_MS: "150" });

    await waitForBackendReady({
      ping: () => pingBackendHttp({ host: "127.0.0.1", port }, "/health"),
      isExited: () => ({ exited: false, code: null }),
      timeoutMs: 5_000,
      baseDelayMs: 30,
      maxDelayMs: 200,
      getRecentLogs: () => "",
    });
  });

  it("edge case: rejeita imediatamente quando o processo já morreu, sem esperar timeout", async () => {
    const start = Date.now();

    await expect(
      waitForBackendReady({
        ping: () => Promise.resolve(false),
        isExited: () => ({ exited: true, code: 1 }),
        timeoutMs: 30_000, // se não detectasse o crash cedo, isso demoraria 30s
        baseDelayMs: 30,
        maxDelayMs: 200,
        getRecentLogs: () => "log de erro simulado",
      }),
    ).rejects.toThrow(/encerrou inesperadamente/);

    expect(Date.now() - start).toBeLessThan(1_000);
  });

  it("edge case: timeout quando o processo nunca fica saudável (distinto de crash)", async () => {
    await expect(
      waitForBackendReady({
        ping: () => Promise.resolve(false),
        isExited: () => ({ exited: false, code: null }),
        timeoutMs: 150,
        baseDelayMs: 30,
        maxDelayMs: 50,
        getRecentLogs: () => "",
      }),
    ).rejects.toThrow(/não respondeu em/);
  });
});

// ---------------------------------------------------------------------------
// killBackendTree — encerramento real de um processo
// ---------------------------------------------------------------------------

describe("killBackendTree", () => {
  it("mata o processo real — process.kill(pid, 0) passa a lançar ESRCH", async () => {
    const port = await getFreePort();
    const child = spawnDummy({ TEST_HEALTH_PORT: String(port) });
    await new Promise((resolve) => setTimeout(resolve, 100)); // deixa o processo assentar
    const pid = child.pid!;

    await new Promise<void>((resolve) => {
      if (process.platform === "win32") {
        // No Windows, killBackendTree() usa spawnSync (taskkill) — síncrono,
        // não invoca o callback treeKillFn (esse é só o caminho não-Windows).
        killBackendTree(pid, process.platform, () => {});
        resolve();
      } else {
        killBackendTree(pid, process.platform, (killPid, cb) => {
          treeKill(killPid, () => {
            cb?.();
            resolve();
          });
        });
      }
    });

    await new Promise((resolve) => setTimeout(resolve, 200)); // dá tempo do SO liberar o PID
    expect(() => process.kill(pid, 0)).toThrow();
  });

  // O processo Python spawna o nats-server como seu filho — matar só o pai
  // (sem matar a árvore) deixaria o filho vivo. Este teste replica isso com
  // um "neto" de verdade (child_process.spawn dentro do dummy-backend),
  // confirmando que killBackendTree mata os dois, não só o pai.
  it("mata a árvore inteira — o 'neto' spawnado pelo processo também morre", async () => {
    const port = await getFreePort();
    const parent = spawnDummy({
      TEST_HEALTH_PORT: String(port),
      SPAWN_CHILD: "1",
    });
    const parentPid = parent.pid!;

    const childPid = await new Promise<number>((resolve, reject) => {
      let buffer = "";
      const timeout = setTimeout(
        () => reject(new Error("CHILD_PID nunca apareceu no stdout")),
        5_000,
      );
      parent.stdout!.on("data", (chunk: Buffer) => {
        buffer += chunk.toString();
        const match = /CHILD_PID=(\d+)/.exec(buffer);
        if (match) {
          clearTimeout(timeout);
          resolve(Number(match[1]));
        }
      });
    });

    await new Promise((resolve) => setTimeout(resolve, 100)); // deixa os dois assentarem

    await new Promise<void>((resolve) => {
      if (process.platform === "win32") {
        killBackendTree(parentPid, process.platform, () => {});
        resolve();
      } else {
        killBackendTree(parentPid, process.platform, (killPid, cb) => {
          treeKill(killPid, () => {
            cb?.();
            resolve();
          });
        });
      }
    });

    await new Promise((resolve) => setTimeout(resolve, 300)); // dá tempo do SO liberar os PIDs
    expect(() => process.kill(parentPid, 0)).toThrow();
    expect(() => process.kill(childPid, 0)).toThrow();
  });

  it("edge case: neto já morto antes da chamada não lança", async () => {
    const port = await getFreePort();
    const parent = spawnDummy({
      TEST_HEALTH_PORT: String(port),
      SPAWN_CHILD: "1",
    });
    const parentPid = parent.pid!;

    const childPid = await new Promise<number>((resolve, reject) => {
      let buffer = "";
      const timeout = setTimeout(
        () => reject(new Error("CHILD_PID nunca apareceu no stdout")),
        5_000,
      );
      parent.stdout!.on("data", (chunk: Buffer) => {
        buffer += chunk.toString();
        const match = /CHILD_PID=(\d+)/.exec(buffer);
        if (match) {
          clearTimeout(timeout);
          resolve(Number(match[1]));
        }
      });
    });

    // Mata o neto sozinho, adiantado — killBackendTree não deve lançar ao
    // tentar matar uma árvore que já não tem esse membro vivo.
    await new Promise<void>((resolve) => treeKill(childPid, () => resolve()));
    await new Promise((resolve) => setTimeout(resolve, 100));

    expect(() => {
      killBackendTree(parentPid, process.platform, (killPid, cb) => {
        treeKill(killPid, () => cb?.());
      });
    }).not.toThrow();
  });

  it("edge case: fora do Windows, delega pro treeKillFn passado (não usa taskkill)", async () => {
    const port = await getFreePort();
    const parent = spawnDummy({ TEST_HEALTH_PORT: String(port) });
    const parentPid = parent.pid!;
    let calledWithPid: number | null = null;
    const treeKillFn = (pid: number, cb?: (err?: Error) => void) => {
      calledWithPid = pid;
      cb?.();
    };

    killBackendTree(parentPid, "linux", treeKillFn);

    expect(calledWithPid).toBe(parentPid);
  });
});
