/**
 * settings-store — autoUpdateEnabled (toggle de auto-update do desktop,
 * Preferências → Atualizações). O Electron main process lê esse valor via
 * GET /settings/prefs antes de agendar as checagens periódicas
 * (scheduleAutoUpdateChecks em main.ts) — o setter precisa empurrar pro
 * backend (pushPrefs) pra essa leitura enxergar a mudança, e
 * hydrateFromBackend precisa trazer o valor salvo de volta pro store no boot.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { useSettingsStore, hydrateFromBackend } from "../settings-store";

beforeEach(() => {
  useSettingsStore.getState().resetSettings();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("settings-store — autoUpdateEnabled", () => {
  it("valor padrão é true (auto-update ligado por padrão)", () => {
    expect(useSettingsStore.getState().autoUpdateEnabled).toBe(true);
  });

  it("setAutoUpdateEnabled atualiza o store e empurra pro backend via PATCH /settings/prefs", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    useSettingsStore.getState().setAutoUpdateEnabled(false);

    expect(useSettingsStore.getState().autoUpdateEnabled).toBe(false);
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/settings/prefs",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ autoUpdateEnabled: false }),
        }),
      );
    });
  });

  it("hydrateFromBackend aplica autoUpdateEnabled=false vindo do backend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ autoUpdateEnabled: false }),
      }),
    );

    await hydrateFromBackend();

    expect(useSettingsStore.getState().autoUpdateEnabled).toBe(false);
  });

  it("par de erro: hydrateFromBackend ignora valor não-booleano (mantém o default)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ autoUpdateEnabled: "nao-eh-bool" }),
      }),
    );

    await hydrateFromBackend();

    expect(useSettingsStore.getState().autoUpdateEnabled).toBe(true);
  });
});
