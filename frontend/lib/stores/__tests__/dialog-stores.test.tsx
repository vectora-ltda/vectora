// @vitest-environment jsdom
/**
 * Tests para os stores de diálogo de configurações (open/openAt/setTab).
 *
 * `openAt` também abre o `SettingsOverlay` unificado (settings-overlay-
 * store) — cobertura própria em settings-overlay.test.tsx. Aqui só o
 * comportamento local de cada store (open/tab/subTab).
 */

import { describe, expect, it, beforeEach } from "vitest";
import { useEnvironmentDialogStore } from "../environment-dialog-store";
import { usePreferenciasDialogStore } from "../preferencias-dialog-store";
import { useAdministracaoDialogStore } from "../administracao-dialog-store";

describe("environment-dialog-store", () => {
  beforeEach(() =>
    useEnvironmentDialogStore.setState({ open: false, tab: "integracoes" }),
  );

  it("openAt abre no tab pedido", () => {
    useEnvironmentDialogStore.getState().openAt("provider_routing");
    expect(useEnvironmentDialogStore.getState().open).toBe(true);
    expect(useEnvironmentDialogStore.getState().tab).toBe("provider_routing");
  });

  it("openAt sem argumento cai no default 'integracoes'", () => {
    useEnvironmentDialogStore.getState().openAt();
    expect(useEnvironmentDialogStore.getState().tab).toBe("integracoes");
  });

  it("setOpen e setTab atualizam o estado", () => {
    useEnvironmentDialogStore.getState().setTab("integracoes");
    useEnvironmentDialogStore.getState().setOpen(true);
    expect(useEnvironmentDialogStore.getState().tab).toBe("integracoes");
    expect(useEnvironmentDialogStore.getState().open).toBe(true);
  });
});

describe("preferencias-dialog-store", () => {
  beforeEach(() =>
    usePreferenciasDialogStore.setState({ open: false, tab: "preferencias" }),
  );

  it("openAt abre no tab pedido", () => {
    usePreferenciasDialogStore.getState().openAt("memoria");
    expect(usePreferenciasDialogStore.getState().open).toBe(true);
    expect(usePreferenciasDialogStore.getState().tab).toBe("memoria");
  });

  it("openAt sem argumento cai no default 'preferencias'", () => {
    usePreferenciasDialogStore.getState().openAt();
    expect(usePreferenciasDialogStore.getState().tab).toBe("preferencias");
  });
});

describe("administracao-dialog-store", () => {
  beforeEach(() =>
    useAdministracaoDialogStore.setState({ open: false, subTab: undefined }),
  );

  it("openAt abre na sub-aba pedida", () => {
    useAdministracaoDialogStore.getState().openAt("storage");
    expect(useAdministracaoDialogStore.getState().open).toBe(true);
    expect(useAdministracaoDialogStore.getState().subTab).toBe("storage");
  });

  it("setSubTab pode limpar a sub-aba (undefined)", () => {
    useAdministracaoDialogStore.getState().setSubTab("users");
    useAdministracaoDialogStore.getState().setSubTab(undefined);
    expect(useAdministracaoDialogStore.getState().subTab).toBeUndefined();
  });
});
