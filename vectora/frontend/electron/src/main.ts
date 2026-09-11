/**
 * Vectora Desktop — main process.
 *
 * Responsabilidades:
 * - Spawn do binário Nuitka (``vectora-core``) como sidecar (VECTORA_DESKTOP=1).
 * - Transporte IPC: unix socket (Linux/macOS) / TCP loopback (Windows). A SPA
 *   carrega de ``vectora-app://app/`` e o main encaminha tudo ao backend, sem
 *   expor porta TCP no desktop (em Linux/macOS).
 * - Health-check com retry exponencial antes de carregar a janela.
 * - Tray icon com menu (Open / Restart Backend / Quit).
 * - Deep-link ``vectora://`` (signin magic-link, deep-link de workspace).
 * - IPC tipado (``contextBridge``) exposto via ``preload.ts``.
 * - Auto-update via ``electron-updater`` repassando estado para o renderer.
 * - Crash handler com diálogo "Reiniciar" e relay para Sentry quando ligado.
 *
 * Não roda lógica de negócio — é casca nativa que orquestra o backend.
 */

import { ChildProcess } from "child_process";
import * as fs from "fs";
import {
  app,
  BrowserWindow,
  desktopCapturer,
  Menu,
  screen,
  session,
  Tray,
  dialog,
  ipcMain,
  nativeImage,
  protocol,
  safeStorage,
  shell,
  WebContentsView,
} from "electron";
import { autoUpdater } from "electron-updater";
import * as http from "http";
import * as os from "os";
import * as path from "path";
import { randomUUID } from "crypto";
import { Readable } from "stream";
// tree-kill ships its own types — no @types/tree-kill needed.
import treeKill = require("tree-kill");
import { parseSetCookieHeader, buildCookieHeader } from "./cookie-utils.js";
import {
  getFreePort,
  backendPath,
  natsBinaryPath,
  spawnBackendProcess,
  IpcPipeParser,
  pingBackendHttp,
  fetchBackendJson,
  postBackendJson,
  waitForBackendReady,
  killBackendTree,
  resolveExternalBackendConnection,
} from "./backend-lifecycle.js";
import {
  BrowserViewManager,
  type ManagedView,
  type ViewBounds,
} from "./browser-view-manager.js";
import { computeDefaultWindowSize } from "./window-size.js";
import {
  fetchMarketplaceThemes,
  searchMarketplaceThemes,
} from "./vscode-marketplace.js";
import {
  createRotatingUpdateBackup,
  listUpdateBackups,
  restoreUpdateBackup,
} from "./update-backup.js";

interface UpdateStatus {
  state:
    | "checking"
    | "available"
    | "downloading"
    | "downloaded"
    | "error"
    | "not-available";
  message?: string;
  progress?: number;
  changelog?: string;
}

// O `name` do package.json é "vectora-desktop" (identificador do pacote npm,
// distinto de outros subprojetos do monorepo) — sem isto, Electron usa esse
// valor pra derivar o userData path (%APPDATA%\vectora-desktop\ no Windows),
// que não é o nome do produto. setName() força %APPDATA%\vectora\ sem
// precisar renomear o pacote. Chamado antes de qualquer path ser resolvido.
app.setName("vectora");

let backend: ChildProcess | null = null;
let backendPort: number | null = null;
let backendPipePath: string | null = null; // Windows named pipe path (\\.\pipe\vectora-<pid>)
let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let pendingDeepLink: string | null = null;
let updateReady = false;
let browserViewManager: BrowserViewManager | null = null;
const desktopBridgeToken =
  process.env.VECTORA_DESKTOP_BRIDGE_TOKEN ?? randomUUID();
let selectedBackupPath: string | null = null;
let pendingBackupPromise: Promise<void> | null = null;
let updateDownloadPromise: Promise<void> | null = null;

function startUpdateDownload(): Promise<void> {
  if (updateDownloadPromise) return updateDownloadPromise;
  if (!pendingBackupPromise) {
    return Promise.reject(new Error("backup da atualização não foi preparado"));
  }
  updateDownloadPromise = pendingBackupPromise.then(async () => {
    await autoUpdater.downloadUpdate();
  });
  return updateDownloadPromise;
}

function pendingUpdatePath(): string {
  return path.join(app.getPath("userData"), "pending-update.json");
}

async function clearPendingUpdate(): Promise<void> {
  await fs.promises
    .rm(pendingUpdatePath(), { force: true })
    .catch(() => undefined);
}

async function rollbackPendingUpdate(): Promise<boolean> {
  try {
    const marker = JSON.parse(
      await fs.promises.readFile(pendingUpdatePath(), "utf8"),
    ) as { backupId?: string };
    if (!marker.backupId) return false;
    const userData = app.getPath("userData");
    const backupRoot = path.join(userData, "update-backups");
    const backup = (await listUpdateBackups(backupRoot)).find(
      (entry) => entry.id === marker.backupId,
    );
    if (!backup) return false;
    await restoreUpdateBackup(backup, userData, backupRoot);
    await clearPendingUpdate();
    return true;
  } catch (error) {
    console.error("[updater] rollback automático falhou", error);
    return false;
  }
}

/**
 * Browser real da aba Browser do workbench (não a SPA) — cada view é um
 * `WebContentsView`, contexto de navegação de nível superior, imune a
 * `X-Frame-Options`/`frame-ancestors` (não é uma sub-frame). Todas as views
 * compartilham `session.fromPartition("persist:browser")`: cookies/cache
 * nativos do Chromium, persistentes em disco, iguais a um perfil de browser
 * real — deliberadamente sem `preload` (são páginas de terceiros, nunca
 * recebem a bridge `window.vectora`).
 */
function getBrowserViewManager(): BrowserViewManager {
  if (browserViewManager) return browserViewManager;
  browserViewManager = new BrowserViewManager({
    createView: () =>
      new WebContentsView({
        webPreferences: {
          contextIsolation: true,
          sandbox: true,
          nodeIntegration: false,
          session: session.fromPartition("persist:browser"),
        },
      }) as unknown as ManagedView,
    attach: (view) => {
      mainWindow?.contentView.addChildView(view as unknown as WebContentsView);
    },
    destroyView: (view) => {
      mainWindow?.contentView.removeChildView(
        view as unknown as WebContentsView,
      );
    },
    emit: (viewId, event) => {
      mainWindow?.webContents.send("vectora:browser-view-event", viewId, event);
    },
  });
  return browserViewManager;
}

const PROTOCOL = "vectora";
const APP_SCHEME = "vectora-app"; // origem da SPA no desktop (IPC, sem TCP)
// 90s: cobre extração do Nuitka onefile no primeiro boot (100-200 MB → %TEMP%)
// e scan do Windows Defender em binário novo não reconhecido.
const READINESS_TIMEOUT_MS = 90_000;
const HEALTH_BASE_DELAY_MS = 200;
const HEALTH_MAX_DELAY_MS = 2_000;

// Evita warning barulhento quando o servidor de update responde 404/erro.
// O app já trata update failure como estado de UI; o processo não deve
// abandonar a inicialização por uma falha transitória ou release ausente.
process.on("unhandledRejection", (reason) => {
  console.warn("[updater] rejeição não tratada capturada", reason);
});

// Últimas linhas de stdout/stderr do backend — exibidas no dialog de erro.
const _backendLog: string[] = [];
const _MAX_LOG_LINES = 60;

// Arquivo que persiste o PID do sidecar backend entre sessões. Permite matar
// processo órfão deixado por crash do Electron sem disparar before-quit.
const _BACKEND_PID_FILE = path.join(os.homedir(), ".vectora", "backend.pid");

async function killStaleBackend(): Promise<void> {
  try {
    const raw = await fs.promises.readFile(_BACKEND_PID_FILE, "utf-8");
    const stalePid = parseInt(raw.trim(), 10);
    if (isNaN(stalePid)) return;
    treeKill(stalePid);
    await new Promise((r) => setTimeout(r, 400));
  } catch {
    // Arquivo não existe ou processo já morreu — normal.
  }
}

// O scheme da SPA precisa ser registrado ANTES de app.whenReady(). Habilita
// fetch/SSE e trata a origem como segura (Secure Context: crypto.randomUUID).
protocol.registerSchemesAsPrivileged([
  {
    scheme: APP_SCHEME,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      stream: true,
      corsEnabled: true,
    },
  },
]);

/**
 * Transporte do backend:
 * - Linux/macOS: unix socket (~/.vectora/vectora.sock)
 * - Windows: named pipe (\\.\pipe\vectora-<pid>) lido de stdout via VECTORA_IPC_PIPE
 * - Fallback Windows (sem pipe ainda pronto): TCP loopback
 */
function backendTransport(): http.RequestOptions {
  if (process.platform !== "win32") {
    return { socketPath: path.join(os.homedir(), ".vectora", "vectora.sock") };
  }
  if (backendPipePath) {
    return { socketPath: backendPipePath };
  }
  return { host: "127.0.0.1", port: backendPort ?? undefined };
}

const _HOP_BY_HOP = new Set([
  "transfer-encoding",
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
]);

// Store in-memory de cookies do backend: injetado manualmente em toda request
// via forwardToBackend porque Chromium não inclui automaticamente cookies de
// session.defaultSession no Cookie header para schemes customizados (vectora-app://).
const _cookieStore = new Map<string, string>();

// Persistência entre reinicializações do app: session.defaultSession.cookies
// NÃO funciona para schemes customizados — Chromium recusa com
// "EXCLUDE_NONCOOKIEABLE_SCHEME" (cookies só são aceitos para schemes padrão
// http/https/ws/wss, mesmo com registerSchemesAsPrivileged). Sem persistência
// nenhuma, o login era perdido a cada restart do app. Grava um arquivo local
// (criptografado via safeStorage — DPAPI no Windows/Keychain no macOS — quando
// disponível) em vez de depender do cookie jar do Chromium.
const _SESSION_STORE_FILE = path.join(os.homedir(), ".vectora", "session.dat");

function persistCookieStore(): void {
  try {
    const json = JSON.stringify(Object.fromEntries(_cookieStore));
    const data = safeStorage.isEncryptionAvailable()
      ? safeStorage.encryptString(json)
      : Buffer.from(json, "utf-8");
    fs.mkdirSync(path.dirname(_SESSION_STORE_FILE), { recursive: true });
    fs.writeFileSync(_SESSION_STORE_FILE, data);
  } catch (err) {
    console.error("[auth] falha ao persistir sessão:", err);
  }
}

function loadPersistedCookieStore(): void {
  try {
    if (!fs.existsSync(_SESSION_STORE_FILE)) return;
    const raw = fs.readFileSync(_SESSION_STORE_FILE);
    const json = safeStorage.isEncryptionAvailable()
      ? safeStorage.decryptString(raw)
      : raw.toString("utf-8");
    const obj = JSON.parse(json) as Record<string, string>;
    for (const [name, value] of Object.entries(obj))
      _cookieStore.set(name, value);
  } catch (err) {
    console.error("[auth] falha ao carregar sessão persistida:", err);
  }
}

async function storeSetCookie(cookieStr: string): Promise<void> {
  const parsed = parseSetCookieHeader(cookieStr);
  if (!parsed) return;
  const { name, attrs, value } = parsed;

  // Max-Age=0 → deletar o cookie (logout / expiração forçada).
  if (attrs["max-age"] !== undefined && parseInt(attrs["max-age"], 10) <= 0) {
    _cookieStore.delete(name);
    persistCookieStore();
    return;
  }

  _cookieStore.set(name, value);
  persistCookieStore();
}

/**
 * Encaminha um request do scheme ``vectora-app://`` para o backend pelo
 * transporte IPC, fazendo streaming da resposta (incl. SSE). ``vectora-app://
 * app/<path>`` → backend ``/<path>``.
 */
async function forwardToBackend(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const headers: Record<string, string> = {};
  request.headers.forEach((value, key) => {
    if (!_HOP_BY_HOP.has(key.toLowerCase())) headers[key] = value;
  });

  // Injeta cookies do store in-memory no header Cookie. Necessário porque
  // Chromium não inclui automaticamente cookies de session.defaultSession
  // nas requests interceptadas por protocol.handle para schemes customizados.
  if (_cookieStore.size > 0) {
    headers["cookie"] = buildCookieHeader(_cookieStore);
  }

  const body =
    request.method !== "GET" && request.method !== "HEAD"
      ? Buffer.from(await request.arrayBuffer())
      : undefined;

  return new Promise<Response>((resolve) => {
    const req = http.request(
      {
        ...backendTransport(),
        method: request.method,
        path: url.pathname + url.search,
        headers,
      },
      (res) => {
        void (async () => {
          const respHeaders = new Headers();
          for (const [key, value] of Object.entries(res.headers)) {
            if (value == null || _HOP_BY_HOP.has(key.toLowerCase())) continue;
            if (key.toLowerCase() === "set-cookie" && Array.isArray(value)) {
              for (const cookie of value) respHeaders.append(key, cookie);
            } else {
              respHeaders.set(
                key,
                Array.isArray(value) ? value.join(", ") : value,
              );
            }
          }
          // Armazena cookies explicitamente no session antes de resolver
          // para que estejam disponíveis na próxima request.
          const setCookies = res.headers["set-cookie"];
          if (Array.isArray(setCookies) && setCookies.length > 0) {
            await Promise.all(setCookies.map(storeSetCookie));
          }
          const stream = Readable.toWeb(res) as unknown as ReadableStream;
          resolve(
            new Response(stream, {
              status: res.statusCode ?? 502,
              headers: respHeaders,
            }),
          );
        })().catch((err) => {
          resolve(
            new Response(`Erro interno: ${(err as Error).message}`, {
              status: 500,
            }),
          );
        });
      },
    );
    req.on("error", (err) =>
      resolve(
        new Response(`Backend indisponível: ${err.message}`, { status: 502 }),
      ),
    );
    if (body) req.write(body);
    req.end();
  });
}

/** Health-check do backend pelo transporte IPC (UDS ou TCP). */
function pingBackend(): Promise<boolean> {
  return pingBackendHttp(backendTransport(), "/health");
}

// ---------------------------------------------------------------------------
// Backend lifecycle — spawn/path/health-check em backend-lifecycle.ts
// (extraído pra ser testável sem `electron`); aqui só a cola com o estado
// do processo main (backend, backendPort, backendPipePath, _backendLog).
// ---------------------------------------------------------------------------

const _resourcesPath = (): string =>
  process.resourcesPath || path.join(__dirname, "..");

async function startBackend(): Promise<void> {
  // Modo backend-primário em dev: quando o backend Python já é o processo
  // primário (`uv run vectora start` rodado direto, fora do Electron) e se
  // autoelegeu, é ELE quem nos spawna — inverte a direção de controle que em
  // produção é sempre Electron→backend. Nesse modo não somos donos do
  // processo: não spawnamos nada, não escrevemos PID file, e `backend` fica
  // `null` pra sempre — o que já faz `restartBackend`/`before-quit`
  // no-oparem sozinhos (ambos são keyed em `backend?.pid`).
  const external = resolveExternalBackendConnection(process.env);
  if (external) {
    backendPort = external.port;
    backendPipePath = external.pipePath;
    return;
  }
  backendPort = await getFreePort();
  const natsBin = natsBinaryPath(
    process.env,
    process.platform,
    _resourcesPath(),
    fs.existsSync,
  );
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    VECTORA_PORT: String(backendPort),
    VECTORA_DESKTOP: "1",
    VECTORA_DESKTOP_BRIDGE_TOKEN: desktopBridgeToken,
    ...(natsBin ? { VECTORA_NATS_BINARY: natsBin } : {}),
  };
  const exePath = backendPath(process.env, process.platform, _resourcesPath());
  backend = spawnBackendProcess(exePath, ["start"], env);
  if (backend.pid) {
    fs.promises
      .writeFile(_BACKEND_PID_FILE, String(backend.pid), "utf-8")
      .catch(() => {});
  }
  const pipeParser = new IpcPipeParser();
  backend.stdout?.on("data", (b: Buffer) => {
    const text = b.toString();
    // Lê VECTORA_IPC_PIPE=<path> do stdout para usar named pipe no Windows —
    // acumula chunks até fechar linha, protegendo contra write parcial do
    // kernel fatiar o marcador entre dois eventos "data".
    const pipe = pipeParser.push(text);
    if (pipe) backendPipePath = pipe;
    _backendLog.push(text);
    if (_backendLog.length > _MAX_LOG_LINES) _backendLog.shift();
    process.stdout.write(`[backend] ${text}`);
  });
  backend.stderr?.on("data", (b: Buffer) => {
    const text = b.toString();
    _backendLog.push(text);
    if (_backendLog.length > _MAX_LOG_LINES) _backendLog.shift();
    process.stderr.write(`[backend] ${text}`);
  });
  backend.on("exit", handleBackendExit);
}

function handleBackendExit(code: number | null, signal: NodeJS.Signals | null) {
  if ((app as unknown as { isQuitting: boolean }).isQuitting) return;
  const logs = _backendLog.slice(-15).join("").trim();
  const detail = `code=${code} signal=${signal ?? "none"}${logs ? `\n\n${logs}` : ""}`;
  console.error(`[backend] saiu inesperadamente: ${detail}`);
  const opts = {
    type: "error" as const,
    title: "Vectora encerrou inesperadamente",
    message: "O backend Vectora encerrou sem aviso.",
    detail,
    buttons: ["Reiniciar", "Sair"],
    defaultId: 0,
    cancelId: 1,
  };
  const action = mainWindow
    ? dialog.showMessageBoxSync(mainWindow, opts)
    : dialog.showMessageBoxSync(opts);
  if (action === 0) {
    void restartBackend();
  } else {
    app.quit();
  }
}

async function restartBackend(): Promise<void> {
  if (backend?.pid) {
    treeKill(backend.pid);
    backend = null;
  }
  backendPipePath = null;
  try {
    await startBackend();
    await waitForBackend();
    if (mainWindow) {
      void mainWindow.loadURL(`${APP_SCHEME}://app/`);
    } else {
      createWindow();
    }
  } catch (err) {
    if (await rollbackPendingUpdate()) {
      app.relaunch();
      app.exit(0);
      return;
    }
    dialog.showErrorBox(
      "Vectora",
      `Falha ao reiniciar backend: ${(err as Error).message}`,
    );
    app.exit(1);
  }
}

/**
 * Health-check com backoff exponencial. Cada falha dobra o delay até
 * ``HEALTH_MAX_DELAY_MS``, capado no ``READINESS_TIMEOUT_MS`` global.
 * Falha imediatamente se o processo backend já terminou (crash no startup).
 */
async function waitForBackend(): Promise<void> {
  await waitForBackendReady({
    ping: pingBackend,
    isExited: () => ({
      exited: backend !== null && backend.exitCode !== null,
      code: backend?.exitCode ?? null,
    }),
    timeoutMs: READINESS_TIMEOUT_MS,
    baseDelayMs: HEALTH_BASE_DELAY_MS,
    maxDelayMs: HEALTH_MAX_DELAY_MS,
    getRecentLogs: () => _backendLog.slice(-20).join("").trim(),
  });
}

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------

function createWindow(): void {
  if (mainWindow) {
    mainWindow.show();
    mainWindow.focus();
    return;
  }
  if (backendPort === null && !backendPipePath)
    throw new Error("Backend não iniciado.");
  // Janela nasce proporcional à tela detectada, não num tamanho fixo — numa
  // tela 1080p+ um default fixo como 1400x900 já nasce quase em tela cheia,
  // prejudicando o modo janela (ver computeDefaultWindowSize).
  const { width, height } = computeDefaultWindowSize(
    screen.getPrimaryDisplay().workAreaSize,
  );
  mainWindow = new BrowserWindow({
    width,
    height,
    minWidth: 800,
    minHeight: 600,
    show: false,
    autoHideMenuBar: true,
    icon: resolveAppIcon(),
    // Titlebar customizada (estilo VS Code, ver src/components/layout/title-bar.tsx
    // no frontend) — a janela nativa não desenha min/max/close nem título; o
    // renderer é responsável pelos controles e pela região arrastável.
    frame: false,
    backgroundColor: "#0a0e1a",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });

  mainWindow.on("maximize", () => {
    mainWindow?.webContents.send("vectora:window-state", { maximized: true });
  });
  mainWindow.on("unmaximize", () => {
    mainWindow?.webContents.send("vectora:window-state", { maximized: false });
  });

  // Inject app version no preload sem precisar recompilar.
  process.env.VECTORA_APP_VERSION = app.getVersion();

  // Carrega a SPA pela origem IPC — zero porta TCP exposta (UDS em Linux/macOS).
  void mainWindow.loadURL(`${APP_SCHEME}://app/`);
  mainWindow.once("ready-to-show", () => {
    mainWindow?.show();
    if (pendingDeepLink) {
      mainWindow?.webContents.send("vectora:deep-link", pendingDeepLink);
      pendingDeepLink = null;
    }
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  // Links externos abrem no navegador padrão (Stripe portal, dashboard).
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });

  // Bloqueia reload/devtools em prod. Permite com VECTORA_DEV=1.
  if (process.env.VECTORA_DEV !== "1") {
    mainWindow.webContents.on("before-input-event", (event, input) => {
      const isReload =
        (input.control || input.meta) && input.key.toLowerCase() === "r";
      const isDevTools =
        (input.control || input.meta) &&
        input.shift &&
        input.key.toLowerCase() === "i";
      if (isReload || isDevTools) event.preventDefault();
    });
  }
}

// ---------------------------------------------------------------------------
// Tray icon
// ---------------------------------------------------------------------------

function getTrayIcon(): Electron.NativeImage {
  // electron-builder copia frontend/public/favicon-32x32.png pra
  // resources/tray-icon.png (ver electron-builder.yml, extraResources); em
  // dev usa o favicon do frontend direto da fonte como fallback.
  const candidates = [
    path.join(process.resourcesPath, "tray-icon.png"),
    path.join(__dirname, "..", "..", "public", "favicon-32x32.png"),
  ];
  for (const c of candidates) {
    const img = nativeImage.createFromPath(c);
    if (!img.isEmpty()) return img;
  }
  return nativeImage.createEmpty();
}

/**
 * Ícone da janela/taskbar. Em produção o electron-builder já embute o
 * ícone no `.exe` compilado (via `icon:` do electron-builder.yml) — mas o
 * binário genérico do pacote npm `electron` usado em dev não tem esse
 * embed, então sem setar `icon:` no BrowserWindow explicitamente a
 * taskbar mostra o ícone padrão do Electron, não o da Vectora.
 */
function resolveAppIcon(): string | undefined {
  const candidates = [
    path.join(process.resourcesPath || "", "vectora.ico"), // produção (extraResources)
    path.join(__dirname, "..", "..", "public", "vectora.ico"), // dev
  ];
  return candidates.find((c) => fs.existsSync(c));
}

function createTray(): void {
  if (tray) return;
  tray = new Tray(getTrayIcon());
  tray.setToolTip("Vectora");
  refreshTrayMenu();
  tray.on("click", () => {
    if (mainWindow) {
      if (mainWindow.isVisible()) mainWindow.focus();
      else mainWindow.show();
    } else {
      createWindow();
    }
  });
}

function refreshTrayMenu(): void {
  if (!tray) return;
  const menu = Menu.buildFromTemplate([
    {
      label: "Abrir Vectora",
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.focus();
        } else {
          createWindow();
        }
      },
    },
    {
      label: "Reiniciar Vectora",
      click: () => {
        app.relaunch();
        app.exit(0);
      },
    },
    ...(updateReady
      ? [
          { type: "separator" as const },
          {
            label: "Aplicar atualização e reiniciar",
            click: () => autoUpdater.quitAndInstall(),
          },
        ]
      : []),
    { type: "separator" },
    {
      label: "Sair",
      click: () => app.quit(),
    },
  ]);
  tray.setContextMenu(menu);
}

// ---------------------------------------------------------------------------
// Deep-link (vectora://)
// ---------------------------------------------------------------------------

function registerDeepLinkProtocol(): void {
  if (process.defaultApp && process.argv.length >= 2) {
    app.setAsDefaultProtocolClient(PROTOCOL, process.execPath, [
      path.resolve(process.argv[1]),
    ]);
  } else {
    app.setAsDefaultProtocolClient(PROTOCOL);
  }
}

function deliverDeepLink(url: string): void {
  if (mainWindow && !mainWindow.webContents.isLoading()) {
    mainWindow.webContents.send("vectora:deep-link", url);
    mainWindow.show();
    mainWindow.focus();
  } else {
    pendingDeepLink = url;
  }
}

// ---------------------------------------------------------------------------
// Auto-updater
// ---------------------------------------------------------------------------

/**
 * Lê `GET /settings/prefs` (mesmo endpoint que o settings-store do frontend
 * usa via `PATCH`/`GET`, chave `autoUpdateEnabled` em
 * `_ALLOWED_FRONTEND_PREF_KEYS`, `backend/workspace/runtime_settings.py`)
 * pra decidir se agenda as checagens automáticas periódicas. Falha (rede,
 * JSON inválido, campo ausente) → `true` (fail-open, mesmo default do
 * settings-store) — nunca deixa o usuário sem update por causa de um erro
 * de leitura transitório.
 */
async function isAutoUpdateEnabled(): Promise<boolean> {
  const prefs = await fetchBackendJson<{ autoUpdateEnabled?: boolean }>(
    backendTransport(),
    "/settings/prefs",
  );
  return prefs?.autoUpdateEnabled !== false;
}

/**
 * Registra os listeners do `electron-updater` e propaga o estado pro
 * renderer via `vectora:update-status` (consumido em `UpdateBanner`,
 * `frontend/components/layout/update-banner.tsx`). Sempre chamado — inclusive
 * com auto-update desligado nas Preferências — porque uma checagem manual
 * (`vectora:check-for-update`, ver `registerIpc()`) também precisa desses
 * listeners pra a UI mostrar o resultado.
 */
function setupAutoUpdater(): void {
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;

  let latestStatus: UpdateStatus = { state: "not-available" };

  const broadcast = (status: UpdateStatus) => {
    latestStatus = { ...latestStatus, ...status };
    mainWindow?.webContents.send("vectora:update-status", latestStatus);
  };

  autoUpdater.on("checking-for-update", () => broadcast({ state: "checking" }));
  autoUpdater.on("update-available", (info) => {
    const notes = Array.isArray(info.releaseNotes)
      ? info.releaseNotes
          .map((note) => note.note)
          .filter(Boolean)
          .join("\n")
      : (info.releaseNotes ?? "");
    updateDownloadPromise = null;
    pendingBackupPromise = createRotatingUpdateBackup(
      app.getPath("userData"),
      path.join(app.getPath("userData"), "update-backups"),
      app.getVersion(),
    ).then(async (backup) => {
      await fs.promises.writeFile(
        pendingUpdatePath(),
        JSON.stringify({ version: info.version, backupId: backup.id }, null, 2),
        { mode: 0o600 },
      );
    });
    broadcast({ state: "available", message: info.version, changelog: notes });
    void startUpdateDownload().catch((error: unknown) => {
      broadcast({
        state: "error",
        message: `Backup local falhou: ${String(error)}`,
      });
    });
  });
  autoUpdater.on("update-not-available", () =>
    broadcast({ state: "not-available" }),
  );
  autoUpdater.on("download-progress", (p) =>
    broadcast({ state: "downloading", progress: p.percent }),
  );
  autoUpdater.on("update-downloaded", () => {
    updateReady = true;
    refreshTrayMenu();
    broadcast({ state: "downloaded" });
  });
  autoUpdater.on("error", (err) =>
    broadcast({ state: "error", message: err.message }),
  );
}

async function safeCheckForUpdates(): Promise<void> {
  try {
    await autoUpdater.checkForUpdates();
  } catch (err) {
    console.warn("[updater] checkForUpdates falhou", err);
  }
}

/**
 * Agenda as checagens automáticas periódicas: 30s após boot (deixa o backend
 * subir primeiro) e depois a cada 6h. Só chamada quando `autoUpdateEnabled`
 * está ligado (Preferências → Atualizações) — a checagem manual continua
 * disponível independente disso, ver `registerIpc()`.
 */
function scheduleAutoUpdateChecks(): void {
  setTimeout(() => {
    void safeCheckForUpdates();
  }, 30_000);
  setInterval(() => void safeCheckForUpdates(), 6 * 60 * 60 * 1000);
}

// ---------------------------------------------------------------------------
// IPC handlers (responde ao preload bridge)
// ---------------------------------------------------------------------------

function registerIpc(): void {
  ipcMain.handle("vectora:open-external", (_event, url: string) =>
    shell.openExternal(url),
  );
  // Seletor nativo de pasta (Pastas Seguras). Devolve `null` no cancelamento —
  // o renderer distingue "cancelou" de "escolheu" pra não limpar o campo.
  ipcMain.handle("vectora:pick-directory", async () => {
    const opts: Electron.OpenDialogOptions = {
      properties: ["openDirectory", "createDirectory"],
    };
    const result = mainWindow
      ? await dialog.showOpenDialog(mainWindow, opts)
      : await dialog.showOpenDialog(opts);
    if (result.canceled || result.filePaths.length === 0) return null;
    return result.filePaths[0];
  });
  ipcMain.handle("vectora:backup-pick-and-inspect", async () => {
    const opts: Electron.OpenDialogOptions = {
      properties: ["openFile"],
      filters: [{ name: "Backup Vectora", extensions: ["zip", "gz"] }],
    };
    const result = mainWindow
      ? await dialog.showOpenDialog(mainWindow, opts)
      : await dialog.showOpenDialog(opts);
    if (result.canceled || result.filePaths.length === 0) return null;
    const archivePath = result.filePaths[0];
    try {
      const preview = await postBackendJson(
        backendTransport(),
        "/storage/backup/inspect",
        { archive_path: archivePath },
        { "x-vectora-desktop-bridge": desktopBridgeToken },
      );
      if (!preview) return null;
      selectedBackupPath = archivePath;
      return preview;
    } finally {
      if (!selectedBackupPath) selectedBackupPath = null;
    }
  });
  ipcMain.handle(
    "vectora:backup-restore",
    async (_event, categories: string[]) => {
      if (!selectedBackupPath) return null;
      try {
        return await postBackendJson(
          backendTransport(),
          "/storage/backup/restore",
          { archive_path: selectedBackupPath, categories, confirmed: true },
          { "x-vectora-desktop-bridge": desktopBridgeToken },
        );
      } finally {
        selectedBackupPath = null;
      }
    },
  );
  ipcMain.handle("vectora:capture-screenshot", async () => {
    const sources = await desktopCapturer.getSources({
      types: ["screen", "window"],
      thumbnailSize: { width: 1920, height: 1080 },
    });
    if (sources.length === 0) return null;
    const result = mainWindow
      ? await dialog.showMessageBox(mainWindow, {
          type: "question",
          buttons: [...sources.map((source) => source.name), "Cancelar"],
          cancelId: sources.length,
          title: "Capturar screenshot",
          message: "Escolha a tela ou janela que deseja anexar.",
        })
      : await dialog.showMessageBox({
          type: "question",
          buttons: [...sources.map((source) => source.name), "Cancelar"],
          cancelId: sources.length,
          title: "Capturar screenshot",
          message: "Escolha a tela ou janela que deseja anexar.",
        });
    if (result.response === sources.length) return null;
    const source = sources[result.response];
    return source ? source.thumbnail.toPNG() : null;
  });
  ipcMain.on("vectora:deep-link-ack", (_event, url: string) => {
    console.log(`[deep-link] renderer ack: ${url}`);
  });
  ipcMain.on("vectora:quit-and-install", () => {
    if (updateReady) autoUpdater.quitAndInstall();
  });
  // Checagem manual (botão "Verificar atualização agora", Preferências) —
  // independente do toggle de auto-update, que só gate os timers
  // periódicos em scheduleAutoUpdateChecks().
  ipcMain.on("vectora:check-for-update", () => {
    void safeCheckForUpdates();
  });
  ipcMain.handle("vectora:list-update-backups", () =>
    listUpdateBackups(path.join(app.getPath("userData"), "update-backups")),
  );
  ipcMain.handle("vectora:restore-update-backup", (_event, backup: unknown) =>
    restoreUpdateBackup(
      backup as Parameters<typeof restoreUpdateBackup>[0],
      app.getPath("userData"),
      path.join(app.getPath("userData"), "update-backups"),
    ),
  );
  ipcMain.on("vectora:download-update", () => {
    void startUpdateDownload().catch((error: unknown) => {
      console.warn("[updater] downloadUpdate falhou", error);
    });
  });

  // Controles da titlebar customizada (frame: false — ver createWindow()).
  ipcMain.on("vectora:window-minimize", () => mainWindow?.minimize());
  ipcMain.on("vectora:window-maximize-toggle", () => {
    if (!mainWindow) return;
    if (mainWindow.isMaximized()) mainWindow.unmaximize();
    else mainWindow.maximize();
  });
  ipcMain.on("vectora:window-close", () => mainWindow?.close());
  ipcMain.handle(
    "vectora:window-is-maximized",
    () => mainWindow?.isMaximized() ?? false,
  );

  // WebSocket não passa pelo protocol.handle(APP_SCHEME, forwardToBackend)
  // (só cobre fetch/HTTP) — o renderer carrega de vectora-app://, um scheme
  // custom contra o qual `new WebSocket("/caminho")` não resolve pra ws://
  // como resolveria numa origem http:// normal. O backend sempre escuta em
  // TCP loopback (mesmo quando o transporte HTTP principal é UDS/named
  // pipe), então expõe essa origem pro renderer construir URLs absolutas.
  ipcMain.handle("vectora:get-backend-ws-origin", () =>
    backendPort !== null ? `ws://127.0.0.1:${backendPort}` : null,
  );

  // Browser real da aba Browser (ver getBrowserViewManager) — comandos
  // invoke/handle (resposta esperada) + eventos fire-and-forget, mesmo
  // padrão do restante deste arquivo.
  ipcMain.handle("vectora:browser-create-view", () =>
    getBrowserViewManager().createView(),
  );
  ipcMain.on("vectora:browser-destroy-view", (_event, viewId: number) => {
    getBrowserViewManager().destroyView(viewId);
  });
  ipcMain.handle(
    "vectora:browser-navigate",
    (_event, viewId: number, url: string) =>
      getBrowserViewManager().navigate(viewId, url),
  );
  ipcMain.on("vectora:browser-go-back", (_event, viewId: number) => {
    getBrowserViewManager().goBack(viewId);
  });
  ipcMain.on("vectora:browser-go-forward", (_event, viewId: number) => {
    getBrowserViewManager().goForward(viewId);
  });
  ipcMain.on("vectora:browser-reload", (_event, viewId: number) => {
    getBrowserViewManager().reload(viewId);
  });
  ipcMain.on("vectora:browser-stop", (_event, viewId: number) => {
    getBrowserViewManager().stop(viewId);
  });
  ipcMain.on(
    "vectora:browser-set-bounds",
    (_event, viewId: number, bounds: ViewBounds) => {
      getBrowserViewManager().setBounds(viewId, bounds);
    },
  );
  ipcMain.on(
    "vectora:browser-set-visible",
    (_event, viewId: number, visible: boolean) => {
      getBrowserViewManager().setVisible(viewId, visible);
    },
  );

  // Instalação de temas do VS Code Marketplace (Preferências → Aparência) —
  // download + extração rodam aqui (fora do sandbox do renderer); erros
  // propagam pro renderer via rejeição da Promise do invoke.
  ipcMain.handle(
    "vectora:themes-fetch-marketplace",
    (_event, extensionId: string) => fetchMarketplaceThemes(extensionId),
  );
  ipcMain.handle(
    "vectora:themes-search-marketplace",
    (_event, query: string, limit?: number) =>
      searchMarketplaceThemes(query, limit),
  );

  // Zoom nativo (Preferências → Aparência → UI Scale) — mais nítido que o
  // fallback CSS usado no navegador. `factor = 1.2^level` é a própria
  // conversão do Chromium entre zoomLevel e fator visual.
  ipcMain.on("vectora:set-zoom-percent", (_event, percent: number) => {
    if (!mainWindow) return;
    const factor = Math.max(0.5, Math.min(2, percent / 100));
    mainWindow.webContents.setZoomLevel(Math.log(factor) / Math.log(1.2));
  });
  ipcMain.handle("vectora:get-zoom-percent", () => {
    if (!mainWindow) return 100;
    const factor = Math.pow(1.2, mainWindow.webContents.getZoomLevel());
    return Math.round(factor * 100);
  });
}

// ---------------------------------------------------------------------------
// Single instance lock — necessário para deep-link no Windows/Linux
// ---------------------------------------------------------------------------

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
}

app.on("second-instance", (_event, argv) => {
  // Windows passa o deep-link como último argumento.
  const url = argv.find((a) => a.startsWith(`${PROTOCOL}://`));
  if (url) deliverDeepLink(url);
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  } else {
    createWindow();
  }
});

// macOS entrega via "open-url".
app.on("open-url", (event, url) => {
  event.preventDefault();
  if (url.startsWith(`${PROTOCOL}://`)) deliverDeepLink(url);
});

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

(app as unknown as { isQuitting: boolean }).isQuitting = false;

app.whenReady().then(async () => {
  registerDeepLinkProtocol();
  registerIpc();
  // Ponte IPC: serve a SPA e encaminha /auth, /vectora.*, /mcp, SSE… ao backend
  // pelo unix socket (Linux/macOS) ou TCP loopback (Windows).
  protocol.handle(APP_SCHEME, (req) => forwardToBackend(req));

  // Restaura a sessão persistida (arquivo local, ver persistCookieStore) do
  // boot anterior para o _cookieStore in-memory. Sem isso a sessão de login
  // é perdida a cada reinicialização do app.
  loadPersistedCookieStore();

  try {
    await killStaleBackend();
    await startBackend();
    await waitForBackend();
    await clearPendingUpdate();
    createWindow();
    createTray();
    setupAutoUpdater();
    if (await isAutoUpdateEnabled()) {
      scheduleAutoUpdateChecks();
    }
  } catch (err) {
    if (await rollbackPendingUpdate()) {
      app.relaunch();
      app.exit(0);
      return;
    }
    dialog.showErrorBox(
      "Vectora",
      `Falha ao iniciar: ${(err as Error).message}`,
    );
    app.exit(1);
  }
});

app.on("window-all-closed", () => {
  // Mantém app vivo no tray em todas plataformas — usuário usa "Sair" no
  // menu ou Cmd+Q (macOS).
  if (process.platform !== "darwin") {
    // No Windows/Linux, sem tray seria zumbi; com tray, fica disponível.
    if (!tray) app.quit();
  }
});

app.on("before-quit", () => {
  (app as unknown as { isQuitting: boolean }).isQuitting = true;
  if (backend?.pid) {
    const pid = backend.pid;
    backend = null;
    // No Windows, killBackendTree usa spawnSync (bloqueia até o taskkill
    // terminar) — garante que o backend está morto antes do processo
    // Electron sair (treeKill é async e o Electron saía antes, deixando o
    // backend órfão).
    killBackendTree(pid, process.platform, treeKill);
  }
  try {
    fs.unlinkSync(_BACKEND_PID_FILE);
  } catch {}
});

app.on("activate", () => {
  // macOS: clicar no dock recria janela se fechada.
  if (BrowserWindow.getAllWindows().length === 0 && backendPort !== null) {
    createWindow();
  }
});
