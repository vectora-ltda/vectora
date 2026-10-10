/**
 * workbench-store — migração de `splitSize`: v0
 * guardava % (≤100), v1 já era px com default 224, v2 sobe o default pra
 * 280 (casa com sidebarWidth do settings-store, padrão do VS Code).
 */

import { describe, expect, it } from "vitest";
import {
  LEGACY_SPLIT_SIZE_DEFAULT,
  SPLIT_SIZE_DEFAULT,
  migrateSplitSize,
  migrateWorkbenchState,
} from "../workbench-store";

describe("workbench-store — migrateSplitSize", () => {
  it("v0 (%, ≤100) vindo de before-v1 vira o default v1 (224)", () => {
    expect(migrateSplitSize(40, 0)).toBe(LEGACY_SPLIT_SIZE_DEFAULT);
  });

  it("default antigo (224) vindo de v1 sobe pro default novo (280)", () => {
    expect(migrateSplitSize(LEGACY_SPLIT_SIZE_DEFAULT, 1)).toBe(
      SPLIT_SIZE_DEFAULT,
    );
  });

  it("erro/borda: valor escolhido manualmente pelo usuário não é sobrescrito", () => {
    expect(migrateSplitSize(350, 1)).toBe(350);
  });

  it("já na versão atual (2) não sofre nenhuma transformação", () => {
    expect(migrateSplitSize(LEGACY_SPLIT_SIZE_DEFAULT, 2)).toBe(
      LEGACY_SPLIT_SIZE_DEFAULT,
    );
  });

  it("migra a aba diff persistida para Git", () => {
    const migrated = migrateWorkbenchState(
      {
        activeTabByThread: {
          legacy: "diff",
          current: "files",
        } as Record<string, "diff" | "files">,
      },
      2,
    );
    expect(migrated.activeTabByThread).toEqual({
      legacy: "git",
      current: "files",
    });
  });
});
