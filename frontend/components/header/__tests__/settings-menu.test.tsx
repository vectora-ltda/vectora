// @vitest-environment jsdom
/**
 * Testes para SettingsMenu — botão único que abre o `SettingsOverlay`
 * direto na categoria "geral", sem dropdown intermediário (removido a
 * pedido do usuário: clicar em settings deve abrir o painel, não um menu
 * com Preferências/Ambiente/Administração).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

const { openCategoryMock } = vi.hoisted(() => ({
  openCategoryMock: vi.fn(),
}));

vi.mock("@/lib/stores/settings-overlay-store", () => ({
  useSettingsOverlayStore: (selector: (s: unknown) => unknown) =>
    selector({ openCategory: openCategoryMock }),
}));
vi.mock("@/components/settings/settings-overlay", () => ({
  SettingsOverlay: () => null,
}));

const LOCAL_USER = {
  id: "local",
  email: "local@vectora.internal",
  role: "root",
  name: "Bruno",
};

async function renderMenu(authState: {
  user: typeof LOCAL_USER | null;
  isAuthenticated: boolean;
}) {
  vi.doMock("@/lib/stores/auth-store", () => ({
    useAuthStore: (selector: (s: unknown) => unknown) => selector(authState),
  }));
  const { SettingsMenu } = await import("../settings-menu");
  render(<SettingsMenu />);
}

afterEach(() => {
  cleanup();
  vi.resetModules();
  vi.restoreAllMocks();
  openCategoryMock.mockClear();
});

describe("SettingsMenu — botão abre o overlay direto", () => {
  it("mostra o nome do usuário como título e abre a categoria 'geral' ao clicar", async () => {
    await renderMenu({ user: LOCAL_USER, isAuthenticated: true });

    const botao = screen.getByLabelText("Configurações");
    expect(botao).toHaveAttribute("title", "Bruno");

    fireEvent.click(botao);
    expect(openCategoryMock).toHaveBeenCalledWith("geral");
  });

  it("sem usuário ainda, botão continua visível com fallback 'Vectora'", async () => {
    await renderMenu({ user: null, isAuthenticated: false });

    const botao = screen.getByLabelText("Configurações");
    expect(botao).toHaveAttribute("title", "Vectora");
  });
});
