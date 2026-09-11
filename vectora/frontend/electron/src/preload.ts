/**
 * Preload script — bridge tipada entre o renderer (chat web) e o main
 * process do Electron.
 *
 * Exposto no renderer como ``window.vectora`` (via ``contextBridge``).
 * Apenas APIs que precisam de capacidades nativas (deep-link, abrir URL
 * externa, info da plataforma) cruzam o bridge — toda a lógica de
 * negócio continua sendo HTTP/SSE contra o backend Vectora.
 */

import { contextBridge, ipcRenderer } from "electron";
import type { BrowserViewEvent, ViewBounds } from "./browser-view-manager.js";
import type { UpdateBackupEntry } from "./update-backup-types.js";

export interface VectoraDesktopBridge {
  /** "win32" | "darwin" | "linux" — útil para shortcuts e UI condicional. */
  readonly platform: NodeJS.Platform;
  /** Versão do app Electron (lida do package.json no boot). */
  readonly appVersion: string;
  /** Abre uma URL no navegador padrão (Stripe portal, dashboard, docs). */
  openExternal: (url: string) => Promise<void>;
  /** Abre o seletor nativo de pasta. `null` = usuário cancelou. */
  pickDirectory: () => Promise<string | null>;
  /** Notifica o main que o renderer recebeu um deep-link e o processou. */
  acknowledgeDeepLink: (url: string) => void;
  /** Subscreve a deep-links (`vectora://...`) entregues ao app. */
  onDeepLink: (handler: (url: string) => void) => () => void;
  /** Subscreve ao estado do auto-updater (downloading, ready, error). */
  onUpdateStatus: (
    handler: (status: {
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
    }) => void,
  ) => () => void;
  /** Aplica update baixado e reinicia. */
  quitAndInstallUpdate: () => void;
  /** Dispara uma checagem manual de atualização — independente do toggle
   * de auto-update (que só gate os timers automáticos, ver main.ts). */
  checkForUpdate: () => void;
  listUpdateBackups: () => Promise<ReadonlyArray<UpdateBackupEntry>>;
  restoreUpdateBackup: (backup: UpdateBackupEntry) => Promise<void>;
  downloadUpdate: () => void;
  /** Origem `ws://127.0.0.1:{porta}` do backend — necessária porque o
   * renderer carrega de `vectora-app://`, scheme custom contra o qual uma
   * URL relativa de WebSocket não resolve pra `ws://` (só HTTP/fetch passa
   * pelo proxy de protocolo). `null` se o backend ainda não subiu porta. */
  getBackendWsOrigin: () => Promise<string | null>;
  /** Controles da titlebar customizada (frame: false — ver main.ts). */
  windowControls: {
    minimize: () => void;
    maximizeToggle: () => void;
    close: () => void;
    isMaximized: () => Promise<boolean>;
    /** Subscreve a mudanças de estado maximizado/restaurado da janela nativa
     * (ex.: duplo-clique na titlebar, resize manual) — mantém o ícone
     * maximizar/restaurar sincronizado sem polling. */
    onStateChange: (
      handler: (state: { maximized: boolean }) => void,
    ) => () => void;
  };
  /** Browser real da aba Browser do workbench — WebContentsView com sessão
   * própria (cookies/cache persistentes, contexto de navegação de nível
   * superior, imune a X-Frame-Options). Ver electron/src/browser-view-manager.ts. */
  browserView: {
    createView: () => Promise<number>;
    destroyView: (viewId: number) => void;
    navigate: (
      viewId: number,
      url: string,
    ) => Promise<{ ok: boolean; error?: string }>;
    goBack: (viewId: number) => void;
    goForward: (viewId: number) => void;
    reload: (viewId: number) => void;
    stop: (viewId: number) => void;
    setBounds: (viewId: number, bounds: ViewBounds) => void;
    setVisible: (viewId: number, visible: boolean) => void;
    /** Subscreve a eventos de navegação (navigated/titleUpdated/
     * faviconUpdated/loadingChanged/loadFailed) de qualquer view criada. */
    onEvent: (
      handler: (viewId: number, event: BrowserViewEvent) => void,
    ) => () => void;
  };
  /** Busca/instalação de temas do VS Code Marketplace — baixa e
   * descompacta o `.vsix` no processo principal (ver
   * electron/src/vscode-marketplace.ts), fora do sandbox do renderer. */
  themes: {
    fetchMarketplace: (extensionId: string) => Promise<{
      extensionId: string;
      displayName: string;
      themes: { label: string; uiTheme: string; contents: string }[];
    }>;
    searchMarketplace: (
      query: string,
      limit?: number,
    ) => Promise<
      {
        extensionId: string;
        displayName: string;
        publisher: string;
        description: string;
        installs: number;
      }[]
    >;
  };
  /** Zoom nativo (`webContents.setZoomLevel`) — mais nítido que o fallback
   * CSS usado no navegador (ver Preferências → Aparência → UI Scale). */
  zoom: {
    setPercent: (percent: number) => void;
    get: () => Promise<number>;
  };
}

const bridge: VectoraDesktopBridge = {
  platform: process.platform,
  appVersion: process.env.VECTORA_APP_VERSION ?? "0.0.0",
  openExternal: (url) => ipcRenderer.invoke("vectora:open-external", url),
  pickDirectory: () => ipcRenderer.invoke("vectora:pick-directory"),
  acknowledgeDeepLink: (url) => ipcRenderer.send("vectora:deep-link-ack", url),
  onDeepLink: (handler) => {
    const listener = (_event: unknown, url: string) => handler(url);
    ipcRenderer.on("vectora:deep-link", listener);
    return () => {
      ipcRenderer.removeListener("vectora:deep-link", listener);
    };
  },
  onUpdateStatus: (handler) => {
    const listener = (_event: unknown, status: Parameters<typeof handler>[0]) =>
      handler(status);
    ipcRenderer.on("vectora:update-status", listener);
    return () => {
      ipcRenderer.removeListener("vectora:update-status", listener);
    };
  },
  quitAndInstallUpdate: () => ipcRenderer.send("vectora:quit-and-install"),
  checkForUpdate: () => ipcRenderer.send("vectora:check-for-update"),
  listUpdateBackups: () => ipcRenderer.invoke("vectora:list-update-backups"),
  restoreUpdateBackup: (backup) =>
    ipcRenderer.invoke("vectora:restore-update-backup", backup),
  downloadUpdate: () => ipcRenderer.send("vectora:download-update"),
  getBackendWsOrigin: () => ipcRenderer.invoke("vectora:get-backend-ws-origin"),
  windowControls: {
    minimize: () => ipcRenderer.send("vectora:window-minimize"),
    maximizeToggle: () => ipcRenderer.send("vectora:window-maximize-toggle"),
    close: () => ipcRenderer.send("vectora:window-close"),
    isMaximized: () => ipcRenderer.invoke("vectora:window-is-maximized"),
    onStateChange: (handler) => {
      const listener = (
        _event: unknown,
        state: Parameters<typeof handler>[0],
      ) => handler(state);
      ipcRenderer.on("vectora:window-state", listener);
      return () => {
        ipcRenderer.removeListener("vectora:window-state", listener);
      };
    },
  },
  browserView: {
    createView: () => ipcRenderer.invoke("vectora:browser-create-view"),
    destroyView: (viewId) =>
      ipcRenderer.send("vectora:browser-destroy-view", viewId),
    navigate: (viewId, url) =>
      ipcRenderer.invoke("vectora:browser-navigate", viewId, url),
    goBack: (viewId) => ipcRenderer.send("vectora:browser-go-back", viewId),
    goForward: (viewId) =>
      ipcRenderer.send("vectora:browser-go-forward", viewId),
    reload: (viewId) => ipcRenderer.send("vectora:browser-reload", viewId),
    stop: (viewId) => ipcRenderer.send("vectora:browser-stop", viewId),
    setBounds: (viewId, bounds) =>
      ipcRenderer.send("vectora:browser-set-bounds", viewId, bounds),
    setVisible: (viewId, visible) =>
      ipcRenderer.send("vectora:browser-set-visible", viewId, visible),
    onEvent: (handler) => {
      const listener = (
        _event: unknown,
        viewId: number,
        change: BrowserViewEvent,
      ) => handler(viewId, change);
      ipcRenderer.on("vectora:browser-view-event", listener);
      return () => {
        ipcRenderer.removeListener("vectora:browser-view-event", listener);
      };
    },
  },
  themes: {
    fetchMarketplace: (extensionId) =>
      ipcRenderer.invoke("vectora:themes-fetch-marketplace", extensionId),
    searchMarketplace: (query, limit) =>
      ipcRenderer.invoke("vectora:themes-search-marketplace", query, limit),
  },
  zoom: {
    setPercent: (percent) =>
      ipcRenderer.send("vectora:set-zoom-percent", percent),
    get: () => ipcRenderer.invoke("vectora:get-zoom-percent"),
  },
};

contextBridge.exposeInMainWorld("vectora", bridge);
