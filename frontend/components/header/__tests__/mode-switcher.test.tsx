// @vitest-environment jsdom
/**
 * Kanban é o 3º modo de interface, feature pública — sem gate de flag.
 * `width` chega como prop (medida pelo Header, que agora é a única barra
 * de topo) — não há mais medição própria neste componente.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

import { overwriteGetLocale, baseLocale } from "@/lib/paraglide/runtime";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { ModeSwitch } from "../mode-switcher";

beforeEach(() => {
  overwriteGetLocale(() => "pt");
});
afterEach(() => {
  cleanup();
  overwriteGetLocale(() => baseLocale);
});

async function montar(width = 300) {
  render(<ModeSwitch show width={width} />);
  await act(async () => {});
}

describe("ModeSwitch — 3ª posição (Kanban, feature pública)", () => {
  it("Kanban sempre aparece, junto com Assistente e IDE", async () => {
    await montar();

    expect(screen.getByRole("button", { name: /kanban/i })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /assistente/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /ide/i })).toBeInTheDocument();
  });

  it("show=false esconde o seletor inteiro", async () => {
    render(<ModeSwitch show={false} width={300} />);
    await act(async () => {});

    expect(screen.queryByRole("group")).not.toBeInTheDocument();
  });
});

describe("ModeSwitch — colapso responsivo", () => {
  it("largura grande mostra o texto completo, visível (sem sr-only)", async () => {
    await montar(1000);
    const botao = screen.getByRole("button", { name: /assistente/i });
    const label = botao.querySelector('span[data-slot="label"]');
    expect(label?.className).not.toContain("sr-only");
    expect(label?.className).not.toContain("truncate");
  });

  it("largura média trunca o texto (classe truncate), mas mantém visível", async () => {
    await montar(700);
    const botao = screen.getByRole("button", { name: /assistente/i });
    const label = botao.querySelector('span[data-slot="label"]');
    expect(label?.className).toContain("truncate");
    expect(label?.className).not.toContain("sr-only");
  });

  it("largura pequena esconde o texto (sr-only) mas mantém o nome acessível", async () => {
    await montar(100);
    const botao = screen.getByRole("button", { name: /assistente/i });
    const label = botao.querySelector('span[data-slot="label"]');
    expect(label?.className).toContain("sr-only");
    // O botão continua com nome acessível pra leitor de tela — não sumiu,
    // só ficou visualmente oculto.
    expect(botao).toBeInTheDocument();
  });
});

describe("ModeSwitch — cor por modo ativo", () => {
  afterEach(async () => {
    await act(async () => {
      useSettingsStore.getState().setUiMode("ide");
    });
  });

  it("modo kanban ativo ganha classe verde", async () => {
    useSettingsStore.getState().setUiMode("kanban");
    await montar();
    const botao = screen.getByRole("button", { name: /kanban/i });
    expect(botao.className).toContain("git-success");
  });

  it("modo assistente ativo ganha classe azul", async () => {
    useSettingsStore.getState().setUiMode("assistant");
    await montar();
    const botao = screen.getByRole("button", { name: /assistente/i });
    expect(botao.className).toContain("blue");
  });

  it("modo inativo permanece neutro, sem cor de destaque", async () => {
    useSettingsStore.getState().setUiMode("kanban");
    await montar();
    const botao = screen.getByRole("button", { name: /ide/i });
    expect(botao.className).not.toContain("git-success");
    expect(botao.className).not.toContain("blue");
    expect(botao.className).not.toContain("violet");
  });
});
