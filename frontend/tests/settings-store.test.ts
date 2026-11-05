/**
 * settings-store.ts
 *
 * Testa:
 * - Valores padrão
 * - Mutações de estado (setShowToolCalls, setTheme, etc.)
 * - Formato da chave de persistência no localStorage
 * - Reset para defaults
 */

import { describe, it, expect, beforeEach, vi } from "vitest";

const workspaceState = vi.hoisted(() => ({ activeId: null as string | null }));

// O store é a unidade sob teste: a API de preferências, o workspace e a
// configuração de deployment não precisam carregar suas árvores de módulos.
vi.mock("@/lib/api/settings-prefs", () => ({
  fetchPrefs: vi.fn().mockResolvedValue({}),
  fetchPrefsOrNull: vi.fn().mockResolvedValue(null),
  pushPrefs: vi.fn().mockResolvedValue({}),
}));
vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: {
    getState: () => ({ active_id: workspaceState.activeId }),
  },
}));
vi.mock("@/lib/config/deployment-config", () => ({
  getDefaultModel: () => "",
}));

// Mock localStorage antes de importar o store
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
    get length() {
      return Object.keys(store).length;
    },
    key: (index: number) => Object.keys(store)[index] ?? null,
  };
})();

Object.defineProperty(globalThis, "localStorage", {
  value: localStorageMock,
  writable: true,
});

// Importar dinamicamente para pegar módulo fresco a cada teste
async function getStore() {
  // Limpa o cache de módulos do Vitest para cada teste
  vi.resetModules();
  const mod = await import("../lib/stores/settings-store");
  return mod;
}

describe("useSettingsStore — valores padrão", () => {
  beforeEach(() => {
    localStorageMock.clear();
    workspaceState.activeId = null;
    vi.resetModules();
  });

  it("showToolCalls deve ser false por padrão", async () => {
    const { useSettingsStore } = await getStore();
    const state = useSettingsStore.getState();
    expect(state.showToolCalls).toBe(false);
  });

  it("permissionMode deve ser 'ask' por padrão (R2)", async () => {
    const { useSettingsStore } = await getStore();
    expect(useSettingsStore.getState().permissionMode).toBe("ask");
  });

  it("reasoningEffort deve ser 'medium' por padrão (R4)", async () => {
    const { useSettingsStore } = await getStore();
    expect(useSettingsStore.getState().reasoningEffort).toBe("medium");
  });

  it("theme deve ser 'system' por padrão", async () => {
    const { useSettingsStore } = await getStore();
    const state = useSettingsStore.getState();
    expect(state.theme).toBe("system");
  });

  it("customSystemPrompt deve ser string vazia por padrão", async () => {
    const { useSettingsStore } = await getStore();
    const state = useSettingsStore.getState();
    expect(state.customSystemPrompt).toBe("");
  });

  it("Decision Models iniciam sem superfícies implicitamente autorizadas", async () => {
    const { useSettingsStore } = await getStore();
    expect(useSettingsStore.getState().decisionModelSurfaces).toEqual({});
  });
});

describe("useSettingsStore — hidratação remota", () => {
  beforeEach(() => {
    localStorageMock.clear();
    workspaceState.activeId = null;
    vi.resetModules();
  });

  it("ignora hidratação antiga do mesmo workspace após uma edição", async () => {
    workspaceState.activeId = "workspace-a";
    const { hydrateDecisionModelPreferences, useSettingsStore } =
      await getStore();
    const { fetchPrefsOrNull } = await import("../lib/api/settings-prefs");
    let resolve!: (value: { decisionModelProvider: "clef-local" }) => void;
    vi.mocked(fetchPrefsOrNull).mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const pending = hydrateDecisionModelPreferences("workspace-a");
    useSettingsStore.getState().setDecisionModelProvider("jev-api");
    resolve({ decisionModelProvider: "clef-local" });
    await pending;
    expect(useSettingsStore.getState().decisionModelProvider).toBe("jev-api");
  });

  it("uma falha antiga não desfaz uma escolha posterior com o mesmo valor", async () => {
    workspaceState.activeId = "workspace-a";
    const { useSettingsStore } = await getStore();
    const { pushPrefs } = await import("../lib/api/settings-prefs");
    let fail!: (value: boolean) => void;
    vi.mocked(pushPrefs).mockReturnValueOnce(
      new Promise((done) => {
        fail = done;
      }),
    );
    useSettingsStore.getState().setDecisionModelProvider("jev-api");
    useSettingsStore.getState().setDecisionModelProvider("laya-local");
    useSettingsStore.getState().setDecisionModelProvider("jev-api");
    fail(false);
    await Promise.resolve();
    expect(useSettingsStore.getState().decisionModelProvider).toBe("jev-api");
  });

  it("persiste escolhas sucessivas na ordem, mesmo com a primeira resposta atrasada", async () => {
    workspaceState.activeId = "workspace-a";
    const { useSettingsStore } = await getStore();
    const { pushPrefs } = await import("../lib/api/settings-prefs");
    vi.mocked(pushPrefs).mockClear();
    let release!: (saved: boolean) => void;
    vi.mocked(pushPrefs).mockReturnValueOnce(
      new Promise((done) => {
        release = done;
      }),
    );
    useSettingsStore.getState().setDecisionModelProvider("jev-api");
    useSettingsStore.getState().setDecisionModelProvider("laya-local");
    expect(pushPrefs).toHaveBeenCalledTimes(1);
    release(true);
    await vi.waitFor(() => expect(pushPrefs).toHaveBeenCalledTimes(2));
    expect(vi.mocked(pushPrefs).mock.calls.map(([changes]) => changes)).toEqual(
      [
        { decisionModelProvider: "jev-api" },
        { decisionModelProvider: "laya-local" },
      ],
    );
    expect(useSettingsStore.getState().decisionModelProvider).toBe(
      "laya-local",
    );
  });

  it("restaura o último valor confirmado quando duas edições sucessivas falham", async () => {
    workspaceState.activeId = "workspace-a";
    const { useSettingsStore } = await getStore();
    const { pushPrefs } = await import("../lib/api/settings-prefs");
    useSettingsStore.setState({ decisionModelProvider: "clef-local" });
    vi.mocked(pushPrefs)
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(false);
    useSettingsStore.getState().setDecisionModelProvider("jev-api");
    useSettingsStore.getState().setDecisionModelProvider("laya-local");
    await vi.waitFor(() =>
      expect(useSettingsStore.getState().decisionModelProvider).toBe(
        "clef-local",
      ),
    );
  });

  it("aplica as preferências do backend sem reenviar PATCH", async () => {
    const { hydrateFromBackend, useSettingsStore } = await getStore();
    const { fetchPrefs, pushPrefs } = await import("../lib/api/settings-prefs");
    vi.mocked(fetchPrefs).mockResolvedValueOnce({
      decisionModelsEnabled: true,
      decisionModelProvider: "jev-api",
      decisionModelSurfaces: { hitl: true, browser: false },
      transcriptionMode: "local",
      permissionMode: "auto",
    });
    vi.mocked(pushPrefs).mockClear();

    await hydrateFromBackend();

    expect(useSettingsStore.getState()).toMatchObject({
      decisionModelsEnabled: true,
      decisionModelProvider: "jev-api",
      decisionModelSurfaces: { hitl: true, browser: false },
      transcriptionMode: "local",
      permissionMode: "auto",
    });
    expect(pushPrefs).not.toHaveBeenCalled();
  });

  it("hidrata Decision Models por workspace sem persistir novamente", async () => {
    workspaceState.activeId = "workspace-a";
    const { hydrateDecisionModelPreferences, useSettingsStore } =
      await getStore();
    const { fetchPrefsOrNull, pushPrefs } =
      await import("../lib/api/settings-prefs");
    vi.mocked(fetchPrefsOrNull).mockResolvedValueOnce({
      decisionModelsEnabled: true,
      decisionModelProvider: "clef-api",
      decisionModelSurfaces: { hitl: true, browser: false },
      transcriptionMode: "local",
    });
    vi.mocked(pushPrefs).mockClear();

    await hydrateDecisionModelPreferences("workspace-a");

    expect(useSettingsStore.getState()).toMatchObject({
      decisionModelsEnabled: true,
      decisionModelProvider: "clef-api",
      decisionModelSurfaces: { hitl: true, browser: false },
      transcriptionMode: "local",
    });
    expect(fetchPrefsOrNull).toHaveBeenCalledWith("workspace-a");
    expect(pushPrefs).not.toHaveBeenCalled();
  });
});

describe("useSettingsStore — mutações de estado", () => {
  beforeEach(() => {
    localStorageMock.clear();
    vi.resetModules();
  });

  it("setShowToolCalls altera showToolCalls para true", async () => {
    const { useSettingsStore } = await getStore();
    useSettingsStore.getState().setShowToolCalls(true);
    expect(useSettingsStore.getState().showToolCalls).toBe(true);
  });

  it("setTheme aceita 'dark'", async () => {
    const { useSettingsStore } = await getStore();
    useSettingsStore.getState().setTheme("dark");
    expect(useSettingsStore.getState().theme).toBe("dark");
  });

  it("setTheme aceita 'light'", async () => {
    const { useSettingsStore } = await getStore();
    useSettingsStore.getState().setTheme("light");
    expect(useSettingsStore.getState().theme).toBe("light");
  });

  it("setCustomSystemPrompt altera customSystemPrompt", async () => {
    const { useSettingsStore } = await getStore();
    useSettingsStore
      .getState()
      .setCustomSystemPrompt("Responda em bullet points.");
    expect(useSettingsStore.getState().customSystemPrompt).toBe(
      "Responda em bullet points.",
    );
  });

  it("setPermissionMode aceita 'plan' (R2)", async () => {
    const { useSettingsStore } = await getStore();
    useSettingsStore.getState().setPermissionMode("plan");
    expect(useSettingsStore.getState().permissionMode).toBe("plan");
  });

  it("setPermissionMode aceita 'bypass' (R2)", async () => {
    const { useSettingsStore } = await getStore();
    useSettingsStore.getState().setPermissionMode("bypass");
    expect(useSettingsStore.getState().permissionMode).toBe("bypass");
  });

  it("setReasoningEffort aceita 'high' (R4)", async () => {
    const { useSettingsStore } = await getStore();
    useSettingsStore.getState().setReasoningEffort("high");
    expect(useSettingsStore.getState().reasoningEffort).toBe("high");
  });
});

describe("useSettingsStore — reset", () => {
  beforeEach(() => {
    localStorageMock.clear();
    vi.resetModules();
  });

  it("resetSettings restaura todos os valores para os defaults", async () => {
    const { useSettingsStore } = await getStore();
    const s = useSettingsStore.getState();

    // Modifica vários campos
    s.setShowToolCalls(true);
    s.setTheme("dark");
    s.setCustomSystemPrompt("Seja breve.");
    s.setPermissionMode("bypass");
    s.setReasoningEffort("max");

    // Reset
    useSettingsStore.getState().resetSettings();

    const after = useSettingsStore.getState();
    expect(after.showToolCalls).toBe(false);
    expect(after.theme).toBe("system");
    expect(after.customSystemPrompt).toBe("");
    expect(after.permissionMode).toBe("ask");
    expect(after.reasoningEffort).toBe("medium");
  });
});

describe("useSettingsStore — STORAGE_KEY", () => {
  beforeEach(() => {
    localStorageMock.clear();
    vi.resetModules();
  });

  it("exporta SETTINGS_KEY_PREFIX como 'vectora-settings-'", async () => {
    const mod = await getStore();
    expect(mod.SETTINGS_KEY_PREFIX).toBe("vectora-settings-");
  });

  it("getStorageKey(userId) retorna 'vectora-settings-{userId}'", async () => {
    const { getStorageKey } = await getStore();
    expect(getStorageKey("user_abc")).toBe("vectora-settings-user_abc");
    expect(getStorageKey("root")).toBe("vectora-settings-root");
  });

  it("getStorageKey sem userId retorna 'vectora-settings-local'", async () => {
    const { getStorageKey } = await getStore();
    expect(getStorageKey()).toBe("vectora-settings-local");
    expect(getStorageKey(undefined)).toBe("vectora-settings-local");
  });
});
