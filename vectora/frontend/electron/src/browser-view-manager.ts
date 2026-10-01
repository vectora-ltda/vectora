/**
 * Browser real da aba Browser do workbench — WebContentsView independentes
 * (contexto de navegação de nível superior, não uma sub-frame), fora da
 * árvore DOM da SPA. Não depende de `electron` diretamente: quem chama em
 * main.ts injeta as fábricas reais de WebContentsView/session, o que torna
 * este módulo testável com dublês simples (ver
 * __tests__/browser-view-manager.test.ts), no mesmo espírito de
 * backend-lifecycle.ts.
 *
 * Escondido/visível é modelado por bounds zerados, não por uma API
 * `setVisible` da view (a base `View` do Electron não garante uma) — bounds
 * {0,0,0,0} nunca pinta nada, e o último bound real fica guardado pra
 * restaurar quando a aba volta a ficar visível.
 */

export interface ViewBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

const HIDDEN_BOUNDS: ViewBounds = { x: 0, y: 0, width: 0, height: 0 };

export interface ManagedWebContents {
  navigationHistory?: {
    canGoBack(): boolean;
    canGoForward(): boolean;
    goBack(): void;
    goForward(): void;
  };
  loadURL(url: string): Promise<void>;
  goBack(): void;
  goForward(): void;
  reload(): void;
  stop(): void;
  close?(): void;
  canGoBack(): boolean;
  canGoForward(): boolean;
  getURL(): string;
  getTitle(): string;
  on(
    event:
      | "did-navigate"
      | "did-navigate-in-page"
      | "page-title-updated"
      | "page-favicon-updated"
      | "did-start-loading"
      | "did-stop-loading"
      | "did-fail-load"
      | "will-navigate"
      | "will-redirect",
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    listener: (...args: any[]) => void,
  ): void;
  on(
    event: "before-input-event",
    listener: (
      event: { preventDefault(): void },
      input: { type: string; key: string },
    ) => void,
  ): void;
  setWindowOpenHandler?(
    handler: (details: { url: string }) => { action: "allow" | "deny" },
  ): void;
  setZoomLevel?(level: number): void;
}

export interface ManagedView {
  webContents: ManagedWebContents;
  setBounds(bounds: ViewBounds): void;
}

export type BrowserViewKind = "tab";
export interface BrowserViewOptions {
  profileId: string;
  kind: BrowserViewKind;
  allowPopups?: boolean;
  zoomPercent?: number;
  permissionMode?: "allow" | "deny";
}

export type BrowserViewEvent =
  | {
      type: "navigated";
      url: string;
      canGoBack: boolean;
      canGoForward: boolean;
    }
  | { type: "titleUpdated"; title: string }
  | { type: "faviconUpdated"; favicon: string }
  | { type: "loadingChanged"; isLoading: boolean }
  | { type: "escapePressed" }
  | {
      type: "loadFailed";
      errorCode: number;
      errorDescription: string;
      url: string;
    };

export interface BrowserViewManagerDeps {
  createView(
    profileId?: string,
    kind?: BrowserViewKind,
    options?: Pick<
      BrowserViewOptions,
      "allowPopups" | "zoomPercent" | "permissionMode"
    >,
  ): ManagedView;
  attach(view: ManagedView): void;
  destroyView(view: ManagedView): void;
  emit(viewId: number, event: BrowserViewEvent): void;
  clearData?(partition: string): Promise<void>;
}

/** Clears persisted browser storage and HTTP cache for one profile. */
export async function clearBrowserSessionData(session: {
  clearStorageData: () => Promise<void>;
  clearCache: () => Promise<void>;
}): Promise<void> {
  await Promise.all([session.clearStorageData(), session.clearCache()]);
}

interface Entry {
  view: ManagedView;
  visible: boolean;
  bounds: ViewBounds;
  kind: BrowserViewKind;
  ownerId: number | null;
  allowPopups: boolean;
}

const ALLOWED_SCHEMES = new Set(["http:", "https:"]);
function normalizeProfileId(profileId: string | undefined): string {
  const normalized = profileId?.trim();
  return normalized || "default";
}

export function isNavigableUrl(
  raw: string,
  kind: BrowserViewKind = "tab",
): boolean {
  try {
    return ALLOWED_SCHEMES.has(new URL(raw).protocol);
  } catch {
    return false;
  }
}

export class BrowserViewManager {
  private readonly entries = new Map<number, Entry>();
  private nextId = 1;

  constructor(private readonly deps: BrowserViewManagerDeps) {}

  createView(
    profileId?: string,
    kind: BrowserViewKind = "tab",
    ownerId: number | null = null,
    options: Pick<
      BrowserViewOptions,
      "allowPopups" | "zoomPercent" | "permissionMode"
    > = {},
  ): number {
    const normalizedProfileId = normalizeProfileId(profileId);
    const hasOptions = Object.keys(options).length > 0;
    const view = hasOptions
      ? this.deps.createView(normalizedProfileId, kind, options)
      : kind === "tab"
        ? this.deps.createView(normalizedProfileId)
        : this.deps.createView(normalizedProfileId, kind);
    const id = this.nextId++;
    this.entries.set(id, {
      view,
      visible: false,
      bounds: HIDDEN_BOUNDS,
      kind,
      ownerId,
      allowPopups: options.allowPopups === true,
    });
    this.wireEvents(id, view, kind);
    this.deps.attach(view);
    return id;
  }

  setZoomPercent(
    id: number,
    percent: number,
    ownerId: number | null = null,
  ): void {
    const entry = this.entries.get(id);
    if (!entry || !this.owns(entry, ownerId)) return;
    const normalized = Math.max(25, Math.min(500, Math.round(percent)));
    const factor = normalized / 100;
    entry.view.webContents.setZoomLevel?.(Math.log(factor) / Math.log(1.2));
  }

  async clearData(profileId = "default"): Promise<void> {
    await this.deps.clearData?.(
      `persist:browser-${normalizeProfileId(profileId)}`,
    );
  }

  destroyView(id: number, ownerId: number | null = null): void {
    const entry = this.entries.get(id);
    if (!entry || !this.owns(entry, ownerId)) return;
    try {
      this.deps.destroyView(entry.view);
    } catch {
      // A view may already have been detached by Electron during shutdown.
    } finally {
      try {
        entry.view.webContents.close?.();
      } finally {
        this.entries.delete(id);
      }
    }
  }

  navigate(
    id: number,
    url: string,
    ownerId: number | null = null,
  ): { ok: boolean; error?: string } {
    const entry = this.entries.get(id);
    if (!entry) return { ok: false, error: "view inexistente" };
    if (!this.owns(entry, ownerId))
      return { ok: false, error: "view não pertence ao remetente" };
    if (!isNavigableUrl(url, entry.kind)) {
      return { ok: false, error: `esquema não permitido: ${url}` };
    }
    void entry.view.webContents.loadURL(url).catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error);
      this.deps.emit(id, {
        type: "loadFailed",
        errorCode: -2,
        errorDescription: message,
        url,
      });
    });
    return { ok: true };
  }

  goBack(id: number, ownerId: number | null = null): void {
    const entry = this.entries.get(id);
    if (!entry || !this.owns(entry, ownerId)) return;
    const wc = entry.view.webContents;
    if (!wc) return;
    if (wc.navigationHistory) {
      wc.navigationHistory.goBack();
    } else {
      wc.goBack();
    }
  }

  goForward(id: number, ownerId: number | null = null): void {
    const entry = this.entries.get(id);
    if (!entry || !this.owns(entry, ownerId)) return;
    const wc = entry.view.webContents;
    if (!wc) return;
    if (wc.navigationHistory) {
      wc.navigationHistory.goForward();
    } else {
      wc.goForward();
    }
  }

  reload(id: number, ownerId: number | null = null): void {
    const entry = this.entries.get(id);
    if (entry && this.owns(entry, ownerId)) entry.view.webContents.reload();
  }

  stop(id: number, ownerId: number | null = null): void {
    const entry = this.entries.get(id);
    if (entry && this.owns(entry, ownerId)) entry.view.webContents.stop();
  }

  setBounds(
    id: number,
    bounds: ViewBounds,
    ownerId: number | null = null,
  ): void {
    const entry = this.entries.get(id);
    if (!entry || !this.owns(entry, ownerId)) return;
    entry.bounds = bounds;
    if (entry.visible) entry.view.setBounds(bounds);
  }

  setVisible(
    id: number,
    visible: boolean,
    ownerId: number | null = null,
  ): void {
    const entry = this.entries.get(id);
    if (!entry || !this.owns(entry, ownerId)) return;
    entry.visible = visible;
    entry.view.setBounds(visible ? entry.bounds : HIDDEN_BOUNDS);
  }

  private owns(entry: Entry, ownerId: number | null): boolean {
    return (
      entry.ownerId === null || ownerId === null || entry.ownerId === ownerId
    );
  }

  private wireEvents(
    id: number,
    view: ManagedView,
    kind: BrowserViewKind,
  ): void {
    const wc = view.webContents;
    const cancelUnsafeNavigation = (
      event: { preventDefault(): void },
      url: string,
    ) => {
      if (!isNavigableUrl(url, kind)) event.preventDefault();
    };
    wc.on("will-navigate", cancelUnsafeNavigation);
    wc.on("will-redirect", cancelUnsafeNavigation);
    // Popups are denied until they can be created as managed views. Allowing
    // them would bypass the manager's bounds, lifecycle and navigation guards.
    wc.setWindowOpenHandler?.(() => ({
      action: this.entries.get(id)?.allowPopups ? "allow" : "deny",
    }));
    const navigated = () =>
      this.deps.emit(id, {
        type: "navigated",
        url: wc.getURL(),
        canGoBack: wc.navigationHistory
          ? wc.navigationHistory.canGoBack()
          : wc.canGoBack(),
        canGoForward: wc.navigationHistory
          ? wc.navigationHistory.canGoForward()
          : wc.canGoForward(),
      });
    wc.on("did-navigate", navigated);
    wc.on("did-navigate-in-page", navigated);
    wc.on("page-title-updated", (_event, title: string) =>
      this.deps.emit(id, { type: "titleUpdated", title }),
    );
    wc.on("page-favicon-updated", (_event, favicons: string[]) => {
      const favicon = favicons[0];
      if (favicon) this.deps.emit(id, { type: "faviconUpdated", favicon });
    });
    wc.on("did-start-loading", () =>
      this.deps.emit(id, { type: "loadingChanged", isLoading: true }),
    );
    wc.on("did-stop-loading", () =>
      this.deps.emit(id, { type: "loadingChanged", isLoading: false }),
    );
    wc.on(
      "did-fail-load",
      (
        _event,
        errorCode: number,
        errorDescription: string,
        validatedURL: string,
      ) => {
        // -3 = ERR_ABORTED — navegação cancelada por uma navegação seguinte
        // (usuário digitou outra URL antes da primeira terminar de carregar),
        // não é uma falha real, não deve virar erro visível pro usuário.
        if (errorCode === -3) return;
        this.deps.emit(id, {
          type: "loadFailed",
          errorCode,
          errorDescription,
          url: validatedURL,
        });
      },
    );
  }
}
