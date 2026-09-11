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
      | "did-fail-load",
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    listener: (...args: any[]) => void,
  ): void;
}

export interface ManagedView {
  webContents: ManagedWebContents;
  setBounds(bounds: ViewBounds): void;
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
  | {
      type: "loadFailed";
      errorCode: number;
      errorDescription: string;
      url: string;
    };

export interface BrowserViewManagerDeps {
  createView(): ManagedView;
  attach(view: ManagedView): void;
  destroyView(view: ManagedView): void;
  emit(viewId: number, event: BrowserViewEvent): void;
}

interface Entry {
  view: ManagedView;
  visible: boolean;
  bounds: ViewBounds;
}

const ALLOWED_SCHEMES = new Set(["http:", "https:"]);

export function isNavigableUrl(raw: string): boolean {
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

  createView(): number {
    const view = this.deps.createView();
    const id = this.nextId++;
    this.entries.set(id, { view, visible: false, bounds: HIDDEN_BOUNDS });
    this.wireEvents(id, view);
    this.deps.attach(view);
    return id;
  }

  destroyView(id: number): void {
    const entry = this.entries.get(id);
    if (!entry) return;
    this.deps.destroyView(entry.view);
    this.entries.delete(id);
  }

  navigate(id: number, url: string): { ok: boolean; error?: string } {
    const entry = this.entries.get(id);
    if (!entry) return { ok: false, error: "view inexistente" };
    if (!isNavigableUrl(url)) {
      return { ok: false, error: `esquema não permitido: ${url}` };
    }
    void entry.view.webContents.loadURL(url);
    return { ok: true };
  }

  goBack(id: number): void {
    const wc = this.entries.get(id)?.view.webContents;
    if (!wc) return;
    wc.navigationHistory?.goBack() ?? wc.goBack();
  }

  goForward(id: number): void {
    const wc = this.entries.get(id)?.view.webContents;
    if (!wc) return;
    wc.navigationHistory?.goForward() ?? wc.goForward();
  }

  reload(id: number): void {
    this.entries.get(id)?.view.webContents.reload();
  }

  stop(id: number): void {
    this.entries.get(id)?.view.webContents.stop();
  }

  setBounds(id: number, bounds: ViewBounds): void {
    const entry = this.entries.get(id);
    if (!entry) return;
    entry.bounds = bounds;
    if (entry.visible) entry.view.setBounds(bounds);
  }

  setVisible(id: number, visible: boolean): void {
    const entry = this.entries.get(id);
    if (!entry) return;
    entry.visible = visible;
    entry.view.setBounds(visible ? entry.bounds : HIDDEN_BOUNDS);
  }

  private wireEvents(id: number, view: ManagedView): void {
    const wc = view.webContents;
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
