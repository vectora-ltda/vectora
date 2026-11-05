/**
 * settings-store — migração das larguras default de sidebar: 224→280
 * (sidebarWidth) e 256→300 (chatSidebarWidth), padrão mais próximo do
 * VS Code. Só bumpa quem está exatamente no default antigo — largura
 * escolhida manualmente pelo usuário não é sobrescrita. Também clampa
 * qualquer valor fora dos limites atuais, mesmo sem bater com o default
 * legado exato.
 */

import { describe, expect, it } from "vitest";
import {
  LEGACY_CHAT_SIDEBAR_WIDTH_DEFAULT,
  LEGACY_SIDEBAR_WIDTH_DEFAULT,
  migrateSidebarWidths,
} from "../settings-store";

describe("settings-store — migrateSidebarWidths", () => {
  it("bumpa os dois valores quando ambos estão no default antigo", () => {
    const result = migrateSidebarWidths(
      LEGACY_SIDEBAR_WIDTH_DEFAULT,
      LEGACY_CHAT_SIDEBAR_WIDTH_DEFAULT,
    );
    expect(result).toEqual({ sidebarWidth: 280, chatSidebarWidth: 300 });
  });

  it("erro/borda: valor escolhido manualmente pelo usuário não é sobrescrito", () => {
    const result = migrateSidebarWidths(320, 400);
    expect(result).toEqual({ sidebarWidth: 320, chatSidebarWidth: 400 });
  });

  it("valores ausentes/undefined passam direto, sem quebrar", () => {
    const result = migrateSidebarWidths(undefined, undefined);
    expect(result).toEqual({
      sidebarWidth: undefined,
      chatSidebarWidth: undefined,
    });
  });

  it("clampa resquício de arrasto antigo (teto do chat caiu de 800→480) mesmo sem bater com o default legado", () => {
    const result = migrateSidebarWidths(600, 810);
    expect(result).toEqual({ sidebarWidth: 480, chatSidebarWidth: 480 });
  });
});
