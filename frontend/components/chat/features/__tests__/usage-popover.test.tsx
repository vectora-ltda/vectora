// @vitest-environment jsdom
/**
 * O medidor da appbar agrega o consumo real dos providers
 * (`GET /usage/providers`) além da janela de contexto.
 *
 * O invariante que importa: provider com falha aparece **como falha**, não
 * zerado — "0 crédito" faria o usuário achar que não gastou nada.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

import { overwriteGetLocale, baseLocale } from "@/lib/paraglide/runtime";
import { UsagePopover } from "../usage-popover";

function mockUsage(providers: unknown[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () => new Response(JSON.stringify({ providers }), { status: 200 }),
    ),
  );
}

beforeEach(() => {
  overwriteGetLocale(() => "pt");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  overwriteGetLocale(() => baseLocale);
});

async function abrir() {
  render(
    <UsagePopover tokensUsed={100} modelId="google-genai:gemini-2.5-flash" />,
  );
  await act(async () => {
    fireEvent.click(screen.getByRole("button"));
  });
}

describe("UsagePopover — consumo por provider", () => {
  it("mostra o consumo de cada provider configurado", async () => {
    mockUsage([
      {
        provider: "tavily",
        label: "Tavily",
        used: 120,
        limit: 1000,
        remaining: 880,
        plan: "researcher",
        unit: "credits",
        error: null,
      },
    ]);

    await abrir();

    expect(screen.getByText(/Tavily/)).toBeInTheDocument();
    expect(screen.getByText(/120/)).toBeInTheDocument();
  });

  it("provider com erro aparece como indisponível, não como zero", async () => {
    // `used: 0` seria uma mentira — a consulta falhou, o consumo é
    // desconhecido.
    mockUsage([
      {
        provider: "openrouter",
        label: "OpenRouter",
        used: null,
        limit: null,
        remaining: null,
        plan: null,
        unit: "usd",
        error: "rede fora",
      },
    ]);

    await abrir();

    expect(screen.getByText(/indisponível/i)).toBeInTheDocument();
    expect(screen.getByText(/rede fora/)).toBeInTheDocument();
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("sem provider configurado mostra só a janela de contexto", async () => {
    mockUsage([]);

    await abrir();

    expect(screen.getByText(/janela de contexto/i)).toBeInTheDocument();
    expect(screen.queryByText(/consumo por provider/i)).not.toBeInTheDocument();
  });

  it("não busca o consumo enquanto o popover está fechado", async () => {
    // Erro/borda: o composer re-renderiza a cada tecla; buscar aí seria uma
    // chamada por caractere digitado.
    mockUsage([]);

    render(
      <UsagePopover tokensUsed={100} modelId="google-genai:gemini-2.5-flash" />,
    );
    await act(async () => {});

    expect(global.fetch).not.toHaveBeenCalled();
  });
});
