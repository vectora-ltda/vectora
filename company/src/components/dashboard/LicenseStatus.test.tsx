// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { LicenseStatus, LicenseHistory } from "./LicenseStatus";

vi.mock("#/paraglide/messages", () => ({
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

const {
  mockGetSubscription,
  mockGetLicenseHistory,
  mockCreateCheckout,
  mockCreatePortal,
} = vi.hoisted(() => ({
  mockGetSubscription: vi.fn(),
  mockGetLicenseHistory: vi.fn(),
  mockCreateCheckout: vi.fn(),
  mockCreatePortal: vi.fn(),
}));

vi.mock("#/server/fns/subscription", () => ({
  getSubscription: mockGetSubscription,
  getLicenseHistory: mockGetLicenseHistory,
  createCheckout: mockCreateCheckout,
  createPortal: mockCreatePortal,
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

function renderWithClient(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("LicenseStatus", () => {
  it("mostra o CTA de upgrade quando o tier é free", async () => {
    mockGetSubscription.mockResolvedValue({
      tier: "free",
      status: "active",
      started_at: "2026-01-01T00:00:00.000Z",
    });
    renderWithClient(<LicenseStatus />);

    await waitFor(() =>
      expect(screen.getByText("license_cta_upgrade_pro")).toBeInTheDocument(),
    );
  });

  it("mostra 'gerenciar' quando pro e em dia", async () => {
    mockGetSubscription.mockResolvedValue({
      tier: "pro",
      status: "active",
      started_at: "2026-01-01T00:00:00.000Z",
    });
    renderWithClient(<LicenseStatus />);

    await waitFor(() =>
      expect(screen.getByText("license_cta_manage")).toBeInTheDocument(),
    );
  });

  it("mostra CTA de atualizar pagamento quando pro e past_due (edge)", async () => {
    mockGetSubscription.mockResolvedValue({
      tier: "pro",
      status: "past_due",
      started_at: "2026-01-01T00:00:00.000Z",
    });
    renderWithClient(<LicenseStatus />);

    await waitFor(() =>
      expect(
        screen.getByText("license_cta_update_payment"),
      ).toBeInTheDocument(),
    );
  });

  it("cai no badge 'expired' para um status desconhecido do banco (edge — CHECK constraint solto, ex. 'trialing' de contas legadas do modelo antigo)", async () => {
    mockGetSubscription.mockResolvedValue({
      tier: "free",
      status: "trialing",
      started_at: "2026-01-01T00:00:00.000Z",
    });
    renderWithClient(<LicenseStatus />);

    await waitFor(() => expect(screen.getByText("free")).toBeInTheDocument());
    expect(screen.getByText("license_status_expired")).toBeInTheDocument();
  });

  it("renderiza null quando não há assinatura (edge)", async () => {
    mockGetSubscription.mockResolvedValue(null);
    const { container } = renderWithClient(<LicenseStatus />);

    await waitFor(() =>
      expect(container.querySelector(".animate-pulse")).not.toBeInTheDocument(),
    );
    expect(container.querySelector(".max-w-2xl")).not.toBeInTheDocument();
  });
});

describe("LicenseHistory", () => {
  it("mostra mensagem de vazio quando não há histórico (edge)", async () => {
    mockGetLicenseHistory.mockResolvedValue([]);
    renderWithClient(<LicenseHistory />);

    await waitFor(() =>
      expect(screen.getByText("license_no_checks")).toBeInTheDocument(),
    );
  });

  it("renderiza a tabela com o IP mascarado (mantém só os 2 primeiros octetos)", async () => {
    mockGetLicenseHistory.mockResolvedValue([
      {
        id: "1",
        vectora_version: "1.2.3",
        result: "valid",
        ip: "203.0.113.42",
        checked_at: "2026-01-01T00:00:00.000Z",
      },
    ]);
    const { container } = renderWithClient(<LicenseHistory />);

    await waitFor(() =>
      expect(screen.getByText("203.0.*.*")).toBeInTheDocument(),
    );
    expect(screen.getByText("1.2.3")).toBeInTheDocument();
    expect(container.querySelector("th")).toHaveClass("text-start");
  });

  it("mostra '—' quando o IP é null (edge)", async () => {
    mockGetLicenseHistory.mockResolvedValue([
      {
        id: "1",
        vectora_version: "1.2.3",
        result: "invalid",
        ip: null,
        checked_at: "2026-01-01T00:00:00.000Z",
      },
    ]);
    renderWithClient(<LicenseHistory />);

    await waitFor(() => expect(screen.getByText("—")).toBeInTheDocument());
  });
});
