// @vitest-environment jsdom
/**
 * buildSettingsCategoryGroups — gating de role (Administração) e feature flag
 * (Connect).
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

function adminCategoryIds() {
  const groups = buildSettingsCategoryGroups({
    connectEnabled: false,
    isAdmin: true,
  });
  const admin = groups.find((g) => g.id === "administracao");
  return admin?.categories.map((c) => c.id) ?? [];
}

describe("buildSettingsCategoryGroups — Administração achatada (5 categorias, não 1)", () => {
  it("usuário admin vê as 5 categorias, cada uma no rail", () => {
    const ids = adminCategoryIds();
    expect(ids).toEqual([
      "admin_users",
      "admin_tools",
      "admin_saferoots",
      "admin_system",
      "admin_storage",
    ]);
  });

  it("usuário admin Free mantém gerenciamento local de usuários", () => {
    const ids = adminCategoryIds();
    expect(ids).toEqual([
      "admin_users",
      "admin_tools",
      "admin_saferoots",
      "admin_system",
      "admin_storage",
    ]);
    expect(ids).toContain("admin_users");
  });

  it("erro/borda: sem role admin/root, nenhuma categoria de Administração aparece", () => {
    const groups = buildSettingsCategoryGroups({
      connectEnabled: false,
      isAdmin: false,
    });
    expect(groups.find((g) => g.id === "administracao")).toBeUndefined();
  });
});
