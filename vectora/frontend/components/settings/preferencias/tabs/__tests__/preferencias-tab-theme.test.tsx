// @vitest-environment jsdom
/**
 * handleThemeChange e handleModeChange controlam campos independentes do
 * store (themePreset vs theme) — trocar um não pode descartar o outro.
 */

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  within,
} from "@testing-library/react";
import { PreferenciasTab } from "../preferencias-tab";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { m } from "@/lib/paraglide/messages";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  useSettingsStore.setState({ theme: "system", themePreset: "default" });
});

describe("PreferenciasTab — modo e paleta não se contaminam", () => {
  it("sincroniza a variante pareada ao montar em modo 'system'", () => {
    useSettingsStore.setState({ theme: "system", themePreset: "github-dark" });
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));

    render(<PreferenciasTab />);

    expect(useSettingsStore.getState().themePreset).toBe("github-light");
  });

  it("selecionar uma paleta (github-dark) em modo 'system' preserva o modo", () => {
    render(<PreferenciasTab />);
    const card = screen.getByText("GitHub Dark").closest("div")!;
    fireEvent.click(card.querySelector("button")!);

    const state = useSettingsStore.getState();
    expect(state.themePreset).toBe("github-dark");
    expect(state.theme).toBe("system");
  });

  it("toggle claro/escuro/sistema fica embutido no cabeçalho da grade (mesma linha do título)", () => {
    render(<PreferenciasTab />);
    const label = screen.getByText(m.prefs_theme());
    const header = label.parentElement!;
    expect(within(header).getByRole("group")).toBeInTheDocument();
  });

  it("selecionar o modo 'dark' preserva a paleta ativa (github-dark)", () => {
    useSettingsStore.setState({ theme: "system", themePreset: "github-dark" });
    render(<PreferenciasTab />);
    const modeToggle = within(screen.getAllByRole("group")[0]!);
    fireEvent.click(
      modeToggle.getByRole("button", { name: /^dark$|^escuro$/i }),
    );

    const state = useSettingsStore.getState();
    expect(state.theme).toBe("dark");
    expect(state.themePreset).toBe("github-dark");
  });

  it("selecionar 'system' não descarta uma paleta custom ativa", () => {
    useSettingsStore.setState({ theme: "light", themePreset: "custom" });
    render(<PreferenciasTab />);
    const modeToggle = within(screen.getAllByRole("group")[0]!);
    fireEvent.click(
      modeToggle.getByRole("button", { name: /^system|^sistema/i }),
    );

    const state = useSettingsStore.getState();
    expect(state.theme).toBe("system");
    expect(state.themePreset).toBe("custom");
  });

  it("usa dark como fallback ao entrar em system sem matchMedia", () => {
    useSettingsStore.setState({
      theme: "light",
      themePreset: "github-light",
    });
    vi.stubGlobal("matchMedia", undefined);
    render(<PreferenciasTab />);
    const modeToggle = within(screen.getAllByRole("group")[0]!);

    fireEvent.click(
      modeToggle.getByRole("button", { name: /^system|^sistema/i }),
    );

    expect(useSettingsStore.getState().themePreset).toBe("github-dark");
  });

  it("sincroniza a variante clara e filtra a grade ao trocar para light", () => {
    useSettingsStore.setState({
      theme: "dark",
      themePreset: "github-dark",
    });
    render(<PreferenciasTab />);
    const modeToggle = within(screen.getAllByRole("group")[0]!);

    fireEvent.click(
      modeToggle.getByRole("button", { name: /^light$|^claro$/i }),
    );

    expect(useSettingsStore.getState().themePreset).toBe("github-light");
    expect(screen.getByText("GitHub Light")).toBeInTheDocument();
    expect(screen.queryByText("GitHub Dark")).not.toBeInTheDocument();
  });
});
