/**
 * Tipos do bridge ``window.vectora`` exposto pelo preload do desktop
 * Electron (``desktop/src/preload.ts``). Centralizado para evitar
 * declarações divergentes em componentes diferentes.
 */

import type { UpdateBackupEntry } from "./update-backup";

export interface VectoraUpdateStatus {
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

export interface VectoraViewBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type VectoraBrowserViewEvent =
  | {
      type: "navigated";
      url: string;
      canGoBack: boolean;
      canGoForward: boolean;
    }
  | { type: "titleUpdated"; title: string }
  | { type: "faviconUpdated"; favicon: string }
  | { type: "loadingChanged"; isLoading: boolean }
  | {
      type: "loadFailed";
      errorCode: number;
      errorDescription: string;
      url: string;
    };

export interface VectoraVscodeThemeFile {
  extensionId: string;
  displayName: string;
  themes: { label: string; uiTheme: string; contents: string }[];
}

export interface VectoraVscodeMarketplaceSearchItem {
  extensionId: string;
  displayName: string;
  publisher: string;
  description: string;
  installs: number;
}

export interface VectoraDesktopBridge {
  readonly platform?: NodeJS.Platform;
  readonly appVersion?: string;
  openExternal?: (url: string) => Promise<void>;
  /** Seletor nativo de pasta. `null` = cancelado (distinto de pasta escolhida).
   * Opcional como o resto da bridge: no modo web `window.vectora` não existe. */
  pickDirectory?: () => Promise<string | null>;
  backupPickAndInspect?: () => Promise<VectoraBackupPreview | null>;
  backupRestore?: (
    categories: string[],
  ) => Promise<VectoraBackupPreview | null>;
  captureScreenshot?: () => Promise<Uint8Array | null>;
  acknowledgeDeepLink?: (url: string) => void;
  onDeepLink?: (handler: (url: string) => void) => () => void;
  onUpdateStatus?: (
    handler: (status: VectoraUpdateStatus) => void,
  ) => () => void;
  quitAndInstallUpdate?: () => void;
  /** Dispara uma checagem manual de atualização — independente do toggle
   * de auto-update (que só gate os timers automáticos, ver main.ts). */
  checkForUpdate?: () => void;
  listUpdateBackups?: () => Promise<ReadonlyArray<UpdateBackupEntry>>;
  restoreUpdateBackup?: (backup: UpdateBackupEntry) => Promise<void>;
  downloadUpdate?: () => void;
  /** Origem `ws://127.0.0.1:{porta}` do backend — necessária porque o
   * renderer carrega de `vectora-app://`, scheme custom contra o qual uma
   * URL relativa de WebSocket não resolve pra `ws://` (só HTTP/fetch passa
   * pelo proxy de protocolo). `null` se o backend ainda não subiu porta. */
  getBackendWsOrigin?: () => Promise<string | null>;
  windowControls?: {
    minimize: () => void;
    maximizeToggle: () => void;
    close: () => void;
    isMaximized: () => Promise<boolean>;
    onStateChange: (
      handler: (state: { maximized: boolean }) => void,
    ) => () => void;
  };
  /** Browser real da aba Browser do workbench (WebContentsView com sessão
   * própria) — presente só no desktop; sem isso, a aba Browser cai no
   * `<iframe>` de fallback (sujeito a X-Frame-Options). */
  browserView?: {
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
    setBounds: (viewId: number, bounds: VectoraViewBounds) => void;
    setVisible: (viewId: number, visible: boolean) => void;
    onEvent: (
      handler: (viewId: number, event: VectoraBrowserViewEvent) => void,
    ) => () => void;
  };
  /** Instalação de temas do VS Code Marketplace — só existe no desktop
   * Electron (baixa e descompacta o `.vsix` no processo principal, via
   * `https`/`zlib` nativos do Node); em modo navegador/servidor
   * `window.vectora?.themes` é `undefined` e a UI de busca/instalação
   * fica oculta (CORS bloquearia a chamada direta do browser mesmo se
   * tentássemos). */
  themes?: {
    fetchMarketplace: (extensionId: string) => Promise<VectoraVscodeThemeFile>;
    searchMarketplace: (
      query: string,
      limit?: number,
    ) => Promise<VectoraVscodeMarketplaceSearchItem[]>;
  };
  /** Zoom nativo do Electron (`webContents.setZoomLevel`) — mais nítido que
   * o fallback CSS usado no navegador. Ausente em modo web. */
  zoom?: {
    setPercent: (percent: number) => void;
    get: () => Promise<number>;
  };
}

export interface VectoraBackupPreview {
  version: number;
  app_version: string;
  size_bytes: number;
  categories: Record<string, number>;
  compatible: boolean;
  storage_mode: string;
  results?: Record<string, { status: string; count: number }>;
}

declare global {
  interface Window {
    vectora?: VectoraDesktopBridge;
  }
}
