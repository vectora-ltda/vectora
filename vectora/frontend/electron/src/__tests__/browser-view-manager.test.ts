import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  BrowserViewManager,
  clearBrowserSessionData,
  isNavigableUrl,
  type BrowserViewManagerDeps,
  type BrowserViewEvent,
  type ManagedView,
} from "../browser-view-manager.js";

function makeFakeView(): ManagedView & {
  handlers: Record<string, (...args: unknown[]) => void>;
  emitFake(event: string, ...args: unknown[]): void;
  getWindowOpenAction(
    targetUrl: string,
  ): { action: "allow" | "deny" } | undefined;
} {
  const handlers: Record<string, (...args: unknown[]) => void> = {};
  let windowOpenHandler:
    ((details: { url: string }) => { action: "allow" | "deny" }) | undefined;
  let url = "";
  return {
    handlers,
    emitFake(event, ...args) {
      handlers[event]?.(...args);
    },
    getWindowOpenAction(targetUrl: string) {
      return windowOpenHandler?.({ url: targetUrl });
    },
    webContents: {
      loadURL: vi.fn(async (u: string) => {
        url = u;
      }),
      goBack: vi.fn(),
      goForward: vi.fn(),
      reload: vi.fn(),
      stop: vi.fn(),
      canGoBack: vi.fn(() => false),
      canGoForward: vi.fn(() => false),
      getURL: vi.fn(() => url),
      getTitle: vi.fn(() => "título"),
      on: vi.fn((event: string, listener: (...args: unknown[]) => void) => {
        handlers[event] = listener;
      }),
      setWindowOpenHandler: vi.fn((handler) => {
        windowOpenHandler = handler;
      }),
    },
    setBounds: vi.fn(),
  };
}

describe("isNavigableUrl", () => {
  it("aceita http/https", () => {
    expect(isNavigableUrl("https://www.google.com")).toBe(true);
    expect(isNavigableUrl("http://localhost:3000")).toBe(true);
  });

  it("preserva a página interna de configurações do Chromium", () => {
    expect(isNavigableUrl("chrome://settings")).toBe(true);
    expect(isNavigableUrl("chrome://settings/passwords")).toBe(true);
    expect(isNavigableUrl("https://chrome//settings/")).toBe(true);
  });

  it("rejeita esquemas não-http e URLs malformadas", () => {
    expect(isNavigableUrl("")).toBe(false);
    expect(isNavigableUrl("file:///etc/passwd")).toBe(false);
    expect(isNavigableUrl("javascript:alert(1)")).toBe(false);
    expect(isNavigableUrl("não é uma url")).toBe(false);
  });
});

describe("BrowserViewManager", () => {
  let deps: BrowserViewManagerDeps;
  let views: ReturnType<typeof makeFakeView>[];
  let emitted: Array<{ viewId: number; event: BrowserViewEvent }>;
  let manager: BrowserViewManager;

  beforeEach(() => {
    views = [];
    emitted = [];
    deps = {
      createView: vi.fn(() => {
        const v = makeFakeView();
        views.push(v);
        return v;
      }),
      attach: vi.fn(),
      destroyView: vi.fn(),
      emit: vi.fn((viewId: number, event: BrowserViewEvent) => {
        emitted.push({ viewId, event });
      }),
    };
    manager = new BrowserViewManager(deps);
  });

  it("cria uma view por chamada e a anexa via deps.attach", () => {
    const id1 = manager.createView();
    const id2 = manager.createView();
    expect(id1).not.toBe(id2);
    expect(deps.createView).toHaveBeenCalledTimes(2);
    expect(deps.attach).toHaveBeenCalledTimes(2);
  });

  it("propaga o perfil ao criar uma view e permite limpar seus dados", async () => {
    deps.clearData = vi.fn(async () => undefined);
    const id = manager.createView("profile-a");
    expect(id).toBe(1);
    expect(deps.createView).toHaveBeenCalledWith("profile-a");

    await manager.clearData("profile-a");
    expect(deps.clearData).toHaveBeenCalledWith("persist:browser-profile-a");
  });

  it("limpa armazenamento e cache da sessão do perfil", async () => {
    const clearStorageData = vi.fn(async () => undefined);
    const clearCache = vi.fn(async () => undefined);

    await clearBrowserSessionData({ clearStorageData, clearCache });

    expect(clearStorageData).toHaveBeenCalledOnce();
    expect(clearCache).toHaveBeenCalledOnce();
  });

  it("normaliza identificadores vazios para o perfil padrão", async () => {
    deps.clearData = vi.fn(async () => undefined);
    manager.createView("");
    expect(deps.createView).toHaveBeenCalledWith("default");
    await manager.clearData("");
    expect(deps.clearData).toHaveBeenCalledWith("persist:browser-default");
  });

  it("nega popups para manter toda navegação sob controle do manager", () => {
    manager.createView();
    const view = views[0];
    expect(view.getWindowOpenAction("https://example.com")).toEqual({
      action: "deny",
    });
  });

  it("destroi a view via deps.destroyView; id inexistente não quebra", () => {
    const id = manager.createView();
    manager.destroyView(id);
    expect(deps.destroyView).toHaveBeenCalledWith(views[0]);
    expect(() => manager.destroyView(9999)).not.toThrow();
  });

  it("navigate chama loadURL para esquema permitido", () => {
    const id = manager.createView();
    const result = manager.navigate(id, "https://example.com");
    expect(result.ok).toBe(true);
    expect(views[0].webContents.loadURL).toHaveBeenCalledWith(
      "https://example.com",
    );
  });

  it("navigate mantém a URL interna de settings sem convertê-la em HTTPS", () => {
    const id = manager.createView();
    const result = manager.navigate(id, "chrome://settings/");
    expect(result.ok).toBe(true);
    expect(views[0].webContents.loadURL).toHaveBeenCalledWith(
      "chrome://settings/",
    );
  });

  it("navigate rejeita esquema não-http com erro claro, sem tocar loadURL", () => {
    const id = manager.createView();
    const result = manager.navigate(id, "file:///etc/passwd");
    expect(result.ok).toBe(false);
    expect(result.error).toContain("esquema não permitido");
    expect(views[0].webContents.loadURL).not.toHaveBeenCalled();
  });

  it("cancela redirects e navegações nativas fora da allowlist", () => {
    const id = manager.createView();
    const view = views[0];
    const navigateEvent = { preventDefault: vi.fn() };
    const redirectEvent = { preventDefault: vi.fn() };

    view.emitFake("will-navigate", navigateEvent, "file:///tmp/secret");
    view.emitFake("will-redirect", redirectEvent, "javascript:alert(1)");

    expect(navigateEvent.preventDefault).toHaveBeenCalledOnce();
    expect(redirectEvent.preventDefault).toHaveBeenCalledOnce();
    expect(id).toBe(1);
  });

  it("navigate em view inexistente retorna erro em vez de lançar", () => {
    const result = manager.navigate(9999, "https://example.com");
    expect(result.ok).toBe(false);
  });

  it("usa navigationHistory sem chamar a API legada", () => {
    const id = manager.createView();
    const view = views[0];
    const historyBack = vi.fn();
    const historyForward = vi.fn();
    view.webContents.navigationHistory = {
      canGoBack: vi.fn(() => true),
      canGoForward: vi.fn(() => true),
      goBack: historyBack,
      goForward: historyForward,
    };

    manager.goBack(id);
    manager.goForward(id);

    expect(historyBack).toHaveBeenCalledOnce();
    expect(historyForward).toHaveBeenCalledOnce();
    expect(view.webContents.goBack).not.toHaveBeenCalled();
    expect(view.webContents.goForward).not.toHaveBeenCalled();
  });

  it("mantém o fallback legado quando navigationHistory não existe", () => {
    const id = manager.createView();
    const view = views[0];

    manager.goBack(id);
    manager.goForward(id);

    expect(view.webContents.goBack).toHaveBeenCalledOnce();
    expect(view.webContents.goForward).toHaveBeenCalledOnce();
  });

  it("setVisible(false) zera bounds; setVisible(true) restaura o último bound real", () => {
    const id = manager.createView();
    manager.setBounds(id, { x: 1, y: 2, width: 300, height: 400 });
    // view começa invisível — setBounds não deve pintar nada ainda.
    expect(views[0].setBounds).not.toHaveBeenCalled();

    manager.setVisible(id, true);
    expect(views[0].setBounds).toHaveBeenLastCalledWith({
      x: 1,
      y: 2,
      width: 300,
      height: 400,
    });

    manager.setVisible(id, false);
    expect(views[0].setBounds).toHaveBeenLastCalledWith({
      x: 0,
      y: 0,
      width: 0,
      height: 0,
    });
  });

  it("emite navigated/titleUpdated/faviconUpdated/loadingChanged a partir dos eventos nativos", () => {
    const id = manager.createView();
    const view = views[0];
    view.emitFake("did-navigate");
    view.emitFake("page-title-updated", null, "Novo título");
    view.emitFake("page-favicon-updated", null, ["https://x/favicon.ico"]);
    view.emitFake("did-start-loading");
    view.emitFake("did-stop-loading");

    expect(emitted).toEqual([
      {
        viewId: id,
        event: {
          type: "navigated",
          url: "",
          canGoBack: false,
          canGoForward: false,
        },
      },
      { viewId: id, event: { type: "titleUpdated", title: "Novo título" } },
      {
        viewId: id,
        event: { type: "faviconUpdated", favicon: "https://x/favicon.ico" },
      },
      { viewId: id, event: { type: "loadingChanged", isLoading: true } },
      { viewId: id, event: { type: "loadingChanged", isLoading: false } },
    ]);
  });

  it("did-fail-load emite loadFailed, exceto ERR_ABORTED (-3, navegação cancelada por nova navegação)", () => {
    const id = manager.createView();
    const view = views[0];
    view.emitFake("did-fail-load", null, -3, "net::ERR_ABORTED", "https://x");
    expect(emitted).toHaveLength(0);

    view.emitFake(
      "did-fail-load",
      null,
      -105,
      "net::ERR_NAME_NOT_RESOLVED",
      "https://naoexiste.invalid",
    );
    expect(emitted).toEqual([
      {
        viewId: id,
        event: {
          type: "loadFailed",
          errorCode: -105,
          errorDescription: "net::ERR_NAME_NOT_RESOLVED",
          url: "https://naoexiste.invalid",
        },
      },
    ]);
  });
});
