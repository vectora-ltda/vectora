// @vitest-environment jsdom
/**
 * SettingsOverlay — shell único (rail + conteúdo) que substitui os 3
 * `Dialog` independentes (Preferências/Ambiente/Administração). Cobre
 * exatamente o que a reforma prometeu: trocar de categoria nunca
 * desmonta/remonta o Dialog (zero flicker), Skills/Tool Policy
 * (antes órfãos, nunca renderizados por nenhum diálogo) aparecem de
 * verdade, e o rail colapsa pra dropdown em telas estreitas.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  screen,
  fireEvent,
} from "@testing-library/react";
import { overwriteGetLocale, baseLocale } from "@/lib/paraglide/runtime";
import { useSettingsOverlayStore } from "@/lib/stores/settings-overlay-store";
import { useAuthStore } from "@/lib/stores/auth-store";

const { useElementWidthMock } = vi.hoisted(() => ({
  useElementWidthMock: vi.fn(),
}));
vi.mock("@/lib/hooks/use-element-width", () => ({
  useElementWidth: useElementWidthMock,
}));

vi.mock("@/lib/hooks/use-feature-flags", () => ({
  useFeatureFlags: () => ({ enableFeaturesBeta: false }),
}));

vi.mock("@/lib/hooks/use-license-status", () => ({
  useLicenseStatus: () => ({
    status: { configured: true },
    loading: false,
  }),
}));

function stubTab(testId: string) {
  return () => <div data-testid={testId}>{testId}</div>;
}

vi.mock("../preferencias/tabs/preferencias-tab", () => ({
  PreferenciasTab: stubTab("tab-geral"),
}));
vi.mock("../preferencias/tabs/fallbacks-tab", () => ({
  FallbacksTab: stubTab("tab-fallbacks"),
}));
vi.mock("../preferencias/tabs/memoria-tab", () => ({
  MemoriaTab: stubTab("tab-memoria"),
}));
vi.mock("../preferencias/tabs/conta-tab", () => ({
  ContaTab: stubTab("tab-conta"),
}));
vi.mock("../environment/tabs/integracoes-tab", () => ({
  IntegracoesTab: stubTab("tab-integracoes"),
}));
vi.mock("../environment/tabs/provider-routing-tab", () => ({
  ProviderRoutingTab: stubTab("tab-provider-routing"),
}));
vi.mock("../environment/tabs/connect-tab", () => ({
  ConnectTab: stubTab("tab-connect"),
}));
vi.mock("../environment/tabs/skills-tab", () => ({
  SkillsTab: stubTab("tab-skills"),
}));
vi.mock("../environment/tabs/tool-policy-panel", () => ({
  ToolPolicyPanel: stubTab("tab-tool-policy"),
}));
vi.mock("../administracao/admin-tab", () => ({
  UsersPanel: stubTab("tab-admin-users"),
  ToolsPanel: stubTab("tab-admin-tools"),
  SafeRootsPanel: stubTab("tab-admin-saferoots"),
  SystemPanel: stubTab("tab-admin-system"),
  StoragePanel: stubTab("tab-admin-storage"),
}));
vi.mock("../billing-panel", () => ({
  BillingPanel: stubTab("tab-billing"),
}));
vi.mock("../about-panel", () => ({
  AboutPanel: stubTab("tab-about"),
}));

const { SettingsOverlay } = await import("../settings-overlay");

function resetStores(role: string | null = null) {
  useSettingsOverlayStore.setState({ open: true, activeCategory: "geral" });
  useAuthStore.setState({
    user: role ? ({ id: "u1", role, email: "u@x.com" } as never) : null,
    isAuthenticated: !!role,
  });
}

beforeEach(() => {
  overwriteGetLocale(() => "pt");
  useElementWidthMock.mockReturnValue([{ current: null }, 900]);
  resetStores();
});

afterEach(() => {
  cleanup();
  overwriteGetLocale(() => baseLocale);
});

async function montar() {
  render(<SettingsOverlay />);
  await act(async () => {});
}

describe("SettingsOverlay — navegação sem flicker", () => {
  it("trocar de categoria não desmonta o Dialog — só troca o conteúdo", async () => {
    await montar();
    const dialog = screen.getByRole("dialog");

    expect(await screen.findByTestId("tab-geral")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Fallbacks"));
    await act(async () => {});

    // Mesmo elemento de Dialog — nunca desmontou/remontou.
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(await screen.findByTestId("tab-fallbacks")).toBeInTheDocument();
    expect(screen.queryByTestId("tab-geral")).not.toBeInTheDocument();
  });
});

describe("SettingsOverlay — categorias Skills/Tool Policy", () => {
  it("Skills renderiza de verdade dentro do novo shell", async () => {
    await montar();
    fireEvent.click(screen.getByText("Skills"));
    expect(await screen.findByTestId("tab-skills")).toBeInTheDocument();
  });

  it("Tool Policy renderiza de verdade dentro do novo shell", async () => {
    await montar();
    fireEvent.click(screen.getByText("Acesso às ferramentas"));
    expect(await screen.findByTestId("tab-tool-policy")).toBeInTheDocument();
  });
});

describe("SettingsOverlay — gate de role (Administração)", () => {
  it("usuário sem role admin/root não vê a categoria Administração", async () => {
    resetStores("member");
    await montar();
    expect(screen.queryByText("Administração")).not.toBeInTheDocument();
  });

  it("usuário admin vê e consegue abrir as categorias de Administração", async () => {
    resetStores("admin");
    await montar();
    fireEvent.click(screen.getByRole("button", { name: "Ferramentas" }));
    expect(await screen.findByTestId("tab-admin-tools")).toBeInTheDocument();
  });
});

describe("SettingsOverlay — busca por categoria", () => {
  it("filtra o rail pelo texto digitado", async () => {
    await montar();
    fireEvent.change(screen.getByPlaceholderText("Buscar categoria…"), {
      target: { value: "mem" },
    });
    expect(screen.getByText("Memória")).toBeInTheDocument();
    expect(screen.queryByText("Fallbacks")).not.toBeInTheDocument();
  });

  it("erro/borda: busca sem match mostra estado vazio, sem quebrar", async () => {
    await montar();
    fireEvent.change(screen.getByPlaceholderText("Buscar categoria…"), {
      target: { value: "xyz-nao-existe" },
    });
    expect(
      screen.getByText("Nenhuma categoria encontrada."),
    ).toBeInTheDocument();
  });
});

describe("SettingsOverlay — colapso responsivo", () => {
  it("largura estreita mostra dropdown em vez do rail lateral", async () => {
    useElementWidthMock.mockReturnValue([{ current: null }, 400]);
    await montar();

    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("largura larga mostra o rail lateral, sem dropdown", async () => {
    useElementWidthMock.mockReturnValue([{ current: null }, 900]);
    await montar();

    expect(screen.getByRole("navigation")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
});
