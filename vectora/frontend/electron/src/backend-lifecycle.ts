/**
 * Ciclo de vida do backend sidecar — spawn, resolução de path, health-check
 * com backoff e encerramento — extraído de main.ts pra ser testável sem
 * importar `electron` (que só existe dentro do processo main do Electron
 * de verdade). Todo estado que main.ts guardava em variáveis de módulo
 * (backendPort, backendPipePath, o processo em si) passa a ser parâmetro
 * explícito das funções aqui, permitindo testar cada peça isoladamente
 * contra um processo Node real (ver __tests__/backend-lifecycle.test.ts).
 */

import { spawn, spawnSync, ChildProcess } from "child_process";
import * as http from "http";
import * as net from "net";
import * as path from "path";

// ---------------------------------------------------------------------------
// Porta livre
// ---------------------------------------------------------------------------

export function getFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const address = srv.address();
      if (address && typeof address === "object") {
        const port = address.port;
        srv.close(() => resolve(port));
      } else {
        srv.close();
        reject(new Error("Sem porta livre."));
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Resolução de path — funções puras, sem I/O além do fs.existsSync opcional
// ---------------------------------------------------------------------------

export interface PathResolutionEnv {
  VECTORA_CORE_PATH?: string;
  VECTORA_NATS_BINARY?: string;
}

/**
 * Resolve o path do binário Nuitka. Prod: `resourcesPath/vectora-core/`
 * (electron-builder `extraResources`). Dev: override por
 * `env.VECTORA_CORE_PATH` apontando pra um build local.
 */
export function backendPath(
  env: PathResolutionEnv,
  platform: NodeJS.Platform,
  resourcesPath: string,
): string {
  const override = env.VECTORA_CORE_PATH;
  if (override) {
    return path.join(
      override,
      platform === "win32" ? "vectora.exe" : "vectora",
    );
  }
  const exe = platform === "win32" ? "vectora.exe" : "vectora";
  return path.join(resourcesPath, "vectora-core", exe);
}

/**
 * Resolução de conexão pro modo backend-primário em dev: quando o backend
 * Python já é o processo primário e nos spawnou (invertendo a direção usual
 * Electron→backend), ele sinaliza isso via `VECTORA_EXTERNAL_BACKEND=1` e já
 * conhece porta/pipe de antemão — retorna `null` no caminho normal
 * (produção, Electron spawna o backend) pra `startBackend()` seguir seu
 * fluxo de sempre.
 */
export interface ExternalBackendConnection {
  port: number | null;
  pipePath: string | null;
}

export function resolveExternalBackendConnection(env: {
  VECTORA_EXTERNAL_BACKEND?: string;
  VECTORA_PORT?: string;
  VECTORA_IPC_PIPE?: string;
}): ExternalBackendConnection | null {
  if (env.VECTORA_EXTERNAL_BACKEND !== "1") return null;
  return {
    port: Number(env.VECTORA_PORT) || null,
    pipePath: env.VECTORA_IPC_PIPE ?? null,
  };
}

/**
 * Resolve o binário nats-server empacotado (extraResources → resources/).
 * `existsFn` é injetável só pra testes puros sem tocar o filesystem real
 * (produção sempre passa `fs.existsSync`).
 */
export function natsBinaryPath(
  env: PathResolutionEnv,
  platform: NodeJS.Platform,
  resourcesPath: string,
  existsFn: (p: string) => boolean,
): string | null {
  if (env.VECTORA_NATS_BINARY) return env.VECTORA_NATS_BINARY;
  const exe = platform === "win32" ? "nats-server.exe" : "nats-server";
  const p = path.join(resourcesPath, exe);
  return existsFn(p) ? p : null;
}

// ---------------------------------------------------------------------------
// Parsing do handshake VECTORA_IPC_PIPE= no stdout
// ---------------------------------------------------------------------------

/**
 * Extrai `VECTORA_IPC_PIPE=<path>` de um chunk de texto de stdout, se
 * presente. Retorna `null` quando o chunk não contém o marcador — inclusive
 * quando ele está fatiado entre dois chunks (kernel write parcial); nesse
 * caso o chamador precisa acumular chunks antes de tentar o parse de novo
 * (ver `IpcPipeParser` abaixo, que faz exatamente isso).
 */
export function parseIpcPipeFromText(text: string): string | null {
  const match = /VECTORA_IPC_PIPE=(.+)/.exec(text);
  return match ? match[1].trim() : null;
}

/**
 * Acumula chunks de stdout até fechar uma linha completa antes de tentar
 * extrair o marcador `VECTORA_IPC_PIPE=` — protege contra o write parcial
 * do kernel entregar `VECTORA_IPC_PIPE=\\.\pi` num chunk e `pe\test\n` no
 * seguinte, caso em que um regex por-chunk isolado (sem acumular) nunca
 * casaria com nenhum dos dois pedaços.
 */
export class IpcPipeParser {
  private buffer = "";
  private found: string | null = null;

  /** Alimenta mais um chunk; retorna o pipe path assim que uma linha
   * completa contendo o marcador for vista (idempotente depois disso).
   *
   * Só tenta o parse quando uma quebra de linha (`\n`) de fato chegou —
   * sem isso, `.+` do regex casa greedily contra o buffer AINDA
   * incompleto (ex.: "VECTORA_IPC_PIPE=\\.\pipe\vectora-" sem o resto)
   * e devolve um pipe path truncado, quebrando o transporte silenciosamente.
   */
  push(chunk: string): string | null {
    if (this.found) return this.found;
    this.buffer += chunk;

    const newlineIdx = this.buffer.indexOf("\n");
    if (newlineIdx === -1) return null; // linha ainda incompleta

    const line = this.buffer.slice(0, newlineIdx);
    this.buffer = this.buffer.slice(newlineIdx + 1);

    const match = parseIpcPipeFromText(line);
    if (match) {
      this.found = match;
      return match;
    }
    // Linha completa sem o marcador — pode haver outra linha completa já
    // no buffer (múltiplas linhas chegaram no mesmo chunk); tenta de novo.
    return this.buffer.includes("\n") ? this.push("") : null;
  }
}

// ---------------------------------------------------------------------------
// Spawn
// ---------------------------------------------------------------------------

export function spawnBackendProcess(
  exePath: string,
  args: string[],
  env: NodeJS.ProcessEnv,
): ChildProcess {
  return spawn(exePath, args, {
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
}

// ---------------------------------------------------------------------------
// Health-check com backoff exponencial
// ---------------------------------------------------------------------------

export function pingBackendHttp(
  transport: http.RequestOptions,
  urlPath: string,
): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.request(
      { ...transport, method: "GET", path: urlPath },
      (res) => {
        res.resume();
        resolve((res.statusCode ?? 500) < 400);
      },
    );
    req.on("error", () => resolve(false));
    req.end();
  });
}

/**
 * GET JSON de um endpoint do backend pelo transporte IPC — usado pra ler
 * settings antes de decidir comportamento do main process (ex.: gate de
 * auto-update em GET /settings/prefs). `null` em qualquer falha (rede,
 * status >= 400, JSON inválido) — o chamador decide o default nesse caso.
 */
export function fetchBackendJson<T = unknown>(
  transport: http.RequestOptions,
  urlPath: string,
): Promise<T | null> {
  return new Promise((resolve) => {
    const req = http.request(
      { ...transport, method: "GET", path: urlPath },
      (res) => {
        if ((res.statusCode ?? 500) >= 400) {
          res.resume();
          resolve(null);
          return;
        }
        let body = "";
        res.on("data", (chunk: Buffer) => {
          body += chunk;
        });
        res.on("end", () => {
          try {
            resolve(JSON.parse(body) as T);
          } catch {
            resolve(null);
          }
        });
      },
    );
    req.on("error", () => resolve(null));
    req.end();
  });
}

/** POST JSON autenticado pela ponte local do processo principal. */
export function postBackendJson<T = unknown>(
  transport: http.RequestOptions,
  urlPath: string,
  payload: unknown,
  headers: Record<string, string> = {},
): Promise<T | null> {
  return new Promise((resolve) => {
    const body = JSON.stringify(payload);
    const req = http.request(
      {
        ...transport,
        method: "POST",
        path: urlPath,
        headers: {
          "content-type": "application/json",
          "content-length": Buffer.byteLength(body),
          ...headers,
        },
      },
      (res) => {
        if ((res.statusCode ?? 500) >= 400) {
          res.resume();
          resolve(null);
          return;
        }
        let response = "";
        res.on("data", (chunk: Buffer) => {
          response += chunk;
        });
        res.on("end", () => {
          try {
            resolve(JSON.parse(response) as T);
          } catch {
            resolve(null);
          }
        });
      },
    );
    req.on("error", () => resolve(null));
    req.write(body);
    req.end();
  });
}

export interface WaitForBackendOptions {
  ping: () => Promise<boolean>;
  isExited: () => { exited: boolean; code: number | null };
  timeoutMs: number;
  baseDelayMs: number;
  maxDelayMs: number;
  getRecentLogs: () => string;
  sleep?: (ms: number) => Promise<void>;
}

/**
 * Health-check com backoff exponencial. Cada falha dobra o delay até
 * `maxDelayMs`, capado em `timeoutMs` no total. Falha imediatamente se o
 * processo já terminou (crash no startup), sem esperar o timeout inteiro.
 */
export async function waitForBackendReady(
  opts: WaitForBackendOptions,
): Promise<void> {
  const sleep =
    opts.sleep ?? ((ms: number) => new Promise((r) => setTimeout(r, ms)));
  const deadline = Date.now() + opts.timeoutMs;
  let delay = opts.baseDelayMs;
  while (Date.now() < deadline) {
    const exitState = opts.isExited();
    if (exitState.exited) {
      const logs = opts.getRecentLogs();
      throw new Error(
        `Backend encerrou inesperadamente (code=${exitState.code}).` +
          (logs ? `\n\nÚltimos logs:\n${logs}` : ""),
      );
    }
    if (await opts.ping()) return;
    await sleep(delay);
    delay = Math.min(delay * 2, opts.maxDelayMs);
  }
  const logs = opts.getRecentLogs();
  throw new Error(
    `Backend não respondeu em ${opts.timeoutMs / 1000}s.` +
      (logs ? `\n\nÚltimos logs:\n${logs}` : ""),
  );
}

// ---------------------------------------------------------------------------
// Encerramento
// ---------------------------------------------------------------------------

/**
 * Mata a árvore de processos do PID informado — `taskkill /T /F` no Windows
 * (síncrono, garante que o processo está morto antes de retornar — o
 * chamador em `before-quit` do Electron depende disso pra não sair antes
 * do backend), `treeKill` (assíncrono) nas demais plataformas.
 */
export function killBackendTree(
  pid: number,
  platform: NodeJS.Platform,
  treeKillFn: (pid: number, callback?: (err?: Error) => void) => void,
): void {
  if (platform === "win32") {
    spawnSync("taskkill", ["/T", "/F", "/PID", String(pid)], {
      stdio: "ignore",
    });
  } else {
    treeKillFn(pid);
  }
}
