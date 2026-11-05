// @vitest-environment jsdom
/**
 * Testes da ConnectTab.
 *
 * Cobre:
 * - Badge Pro visível nos 4 blocos (Telegram, Discord, Slack, Email)
 * - Hidratação do estado real via GET /connect/status ao montar (antes o
 *   badge só refletia o formulário não-salvo, nunca o estado de verdade)
 * - Toggle: liga/desliga chama POST /connect/{platform}/enabled sem
 *   precisar do botão "Salvar"; desabilitado quando não configurado
 * - Salvar configurações preenchidas: POST /auth/envs com o payload certo
 * - Erro/borda: resposta não-ok do backend (ex.: 402 sem Pro) mostra toast
 *   de erro em vez de sucesso silencioso
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  waitFor,
} from "@testing-library/react";

const { useLicenseStatusMock } = vi.hoisted(() => ({
  useLicenseStatusMock: vi.fn(),
}));
const { toastErrorMock, toastSuccessMock, toastWarningMock } = vi.hoisted(
  () => ({
    toastErrorMock: vi.fn(),
    toastSuccessMock: vi.fn(),
    toastWarningMock: vi.fn(),
  }),
);

vi.mock("@/lib/hooks/use-license-status", () => ({
  useLicenseStatus: useLicenseStatusMock,
}));

vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: {
    getState: () => ({
      error: toastErrorMock,
      success: toastSuccessMock,
      warning: toastWarningMock,
    }),
  },
}));

const { ConnectTab } = await import("../connect-tab");

/** Stub padrão de /connect/status — sem plataforma configurada. Testes que
 * precisam de outro estado passam seu próprio `statusResponse`. */
function stubFetch(
  statusResponse: Record<string, unknown> = {},
  extra?: (url: string, options?: RequestInit) => Response | null,
) {
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (extra) {
      const custom = extra(url, options);
      if (custom) return custom;
    }
    if (url === "/connect/status") {
      return { ok: true, json: async () => statusResponse } as Response;
    }
    return { ok: true, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  useLicenseStatusMock.mockReturnValue({
    status: { configured: false, tier: null, status: "unknown" },
    loading: false,
    refetch: vi.fn(),
  });
  toastErrorMock.mockClear();
  toastSuccessMock.mockClear();
  toastWarningMock.mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ConnectTab", () => {
  it("mostra o badge Pro nos 4 blocos de plataforma", async () => {
    stubFetch();
    render(<ConnectTab />);
    await waitFor(() => expect(screen.getAllByText("Pro")).toHaveLength(4));
  });

  it("busca GET /connect/status ao montar e reflete o estado real (não o formulário)", async () => {
    stubFetch({
      discord: { configured: true, enabled: true, running: true },
    });
    render(<ConnectTab />);

    // Badge "Configurado" aparece pro Discord sem nunca ter digitado nada
    // no campo de token — é o estado real do backend, não o form.
    await waitFor(() =>
      expect(screen.getAllByText("Configurado")).toHaveLength(1),
    );
  });

  it("clicar no switch de uma plataforma configurada liga/desliga via POST /connect/{platform}/enabled", async () => {
    const fetchMock = stubFetch({
      telegram: { configured: true, enabled: false, running: false },
    });
    render(<ConnectTab />);

    const toggle = await screen.findByRole("switch", {
      name: "Enable/disable telegram",
    });
    expect(toggle).not.toBeDisabled();

    fireEvent.click(toggle);

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url]) => url === "/connect/telegram/enabled",
      );
      expect(call).toBeTruthy();
    });
    const [, options] = fetchMock.mock.calls.find(
      ([url]) => url === "/connect/telegram/enabled",
    )!;
    expect(JSON.parse((options as RequestInit).body as string)).toEqual({
      enabled: true,
    });
  });

  it("switch fica desabilitado quando a plataforma não está configurada", async () => {
    stubFetch();
    render(<ConnectTab />);

    const toggles = await screen.findAllByRole("switch");
    for (const toggle of toggles) {
      expect(toggle).toBeDisabled();
    }
  });

  it("erro/borda: falha no toggle reverte o estado otimista e mostra toast", async () => {
    const fetchMock = stubFetch(
      { discord: { configured: true, enabled: false, running: false } },
      (url) =>
        url === "/connect/discord/enabled"
          ? ({ ok: false, status: 500 } as Response)
          : null,
    );
    render(<ConnectTab />);

    const toggle = await screen.findByRole("switch", {
      name: "Enable/disable discord",
    });
    fireEvent.click(toggle);

    await waitFor(() => expect(toastErrorMock).toHaveBeenCalled());
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(fetchMock).toHaveBeenCalled();
  });

  it("salva as configurações preenchidas via POST /auth/envs", async () => {
    const fetchMock = stubFetch();

    render(<ConnectTab />);
    fireEvent.change(screen.getByPlaceholderText(/123456789:ABC/), {
      target: { value: "123:abc" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Salvar/ }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => url === "/auth/envs");
      expect(call).toBeTruthy();
    });
    const [, options] = fetchMock.mock.calls.find(
      ([url]) => url === "/auth/envs",
    )!;
    expect(JSON.parse((options as RequestInit).body as string)).toEqual([
      { key: "TELEGRAM_BOT_TOKEN", value: "123:abc" },
    ]);
    await waitFor(() => expect(toastSuccessMock).toHaveBeenCalled());
  });

  it("erro/borda: resposta não-ok (ex.: 402 sem Pro) mostra toast de erro", async () => {
    stubFetch({}, (url) =>
      url === "/auth/envs" ? ({ ok: false, status: 402 } as Response) : null,
    );

    render(<ConnectTab />);
    fireEvent.change(screen.getByPlaceholderText(/123456789:ABC/), {
      target: { value: "123:abc" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Salvar/ }));

    await waitFor(() => expect(toastErrorMock).toHaveBeenCalled());
    expect(toastSuccessMock).not.toHaveBeenCalled();
  });
});
