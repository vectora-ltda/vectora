// @vitest-environment jsdom
/**
 * buildSettingsCategoryGroups — gating de role (Administração) e tier
 * ("Usuários", recurso multi-usuário puro). Antes vivia dentro do
 * `AdminTab` (useLicenseStatus + filtro local); agora é decidido aqui, no
 * mesmo lugar que o gate de role e de feature flag (Connect).
 */

import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get:
        (_t, prop) =>
        (..._args: unknown[]) =>
          String(prop),
    },
  ),
}));

import { buildSettingsCategoryGroups } from "../settings-categories";

function adminCategoryIds(isFree: boolean) {
  const groups = buildSettingsCategoryGroups({
    connectEnabled: false,
    isAdmin: true,
    isFree,
  });
  const admin = groups.find((g) => g.id === "administracao");
  return admin?.categories.map((c) => c.id) ?? [];
}

describe("buildSettingsCategoryGroups — Administração achatada (5 categorias, não 1)", () => {
  it("usuário admin com licença Pro vê as 5 categorias, cada uma no rail", () => {
    const ids = adminCategoryIds(false);
    expect(ids).toEqual([
      "admin_users",
      "admin_tools",
      "admin_saferoots",
      "admin_system",
      "admin_storage",
    ]);
  });

  it("Free (sem licença) esconde só 'Usuários' — as outras 4 continuam", () => {
    const ids = adminCategoryIds(true);
    expect(ids).toEqual([
      "admin_tools",
      "admin_saferoots",
      "admin_system",
      "admin_storage",
    ]);
    expect(ids).not.toContain("admin_users");
  });

  it("erro/borda: sem role admin/root, nenhuma categoria de Administração aparece", () => {
    const groups = buildSettingsCategoryGroups({
      connectEnabled: false,
      isAdmin: false,
      isFree: false,
    });
    expect(groups.find((g) => g.id === "administracao")).toBeUndefined();
  });
});
