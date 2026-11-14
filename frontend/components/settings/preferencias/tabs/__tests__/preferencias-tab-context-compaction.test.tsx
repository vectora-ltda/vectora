// @vitest-environment jsdom
/**
 * PreferenciasTab — compactação automática do histórico: o interruptor mostra e
 * altera a preferência que vai em cada envio do chat.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { PreferenciasTab } from "../preferencias-tab";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { m } from "@/lib/paraglide/messages";

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  useSettingsStore.setState({ contextCompactionEnabled: true });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PreferenciasTab — compactação do contexto", () => {
  it("mostra a seção com o interruptor ligado por padrão", () => {
    render(<PreferenciasTab />);

    expect(screen.getByText(m.prefs_context_compaction_section())).toBeTruthy();
    const toggle = screen.getByRole("switch", {
      name: m.prefs_context_compaction_toggle(),
    });
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(m.prefs_context_compaction_hint())).toBeTruthy();
  });

  it("desligar o interruptor grava a preferência", () => {
    render(<PreferenciasTab />);

    fireEvent.click(
      screen.getByRole("switch", { name: m.prefs_context_compaction_toggle() }),
    );

    expect(useSettingsStore.getState().contextCompactionEnabled).toBe(false);
    expect(
      screen
        .getByRole("switch", { name: m.prefs_context_compaction_toggle() })
        .getAttribute("aria-checked"),
    ).toBe("false");
  });

  it("reflete a preferência já salva como desligada", () => {
    useSettingsStore.setState({ contextCompactionEnabled: false });

    render(<PreferenciasTab />);

    expect(
      screen
        .getByRole("switch", { name: m.prefs_context_compaction_toggle() })
        .getAttribute("aria-checked"),
    ).toBe("false");
  });

  it("ligar de novo volta a enviar a compactação", () => {
    useSettingsStore.setState({ contextCompactionEnabled: false });
    render(<PreferenciasTab />);

    fireEvent.click(
      screen.getByRole("switch", { name: m.prefs_context_compaction_toggle() }),
    );

    expect(useSettingsStore.getState().contextCompactionEnabled).toBe(true);
  });
});
