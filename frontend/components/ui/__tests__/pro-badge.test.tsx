// @vitest-environment jsdom
/**
 * ProBadge: rótulo sempre presente, variante muda com o tier da licença.
 */

import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import type { LicenseStatus } from "@/lib/hooks/use-license-status";

const { useLicenseStatusMock } = vi.hoisted(() => ({
  useLicenseStatusMock: vi.fn(),
}));

vi.mock("@/lib/hooks/use-license-status", () => ({
  useLicenseStatus: useLicenseStatusMock,
}));

const { ProBadge } = await import("../pro-badge");

function licenseStatus(configured: boolean): {
  status: LicenseStatus;
  loading: boolean;
  refetch: () => Promise<void>;
} {
  return {
    status: {
      configured,
      tier: configured ? "pro" : null,
      status: configured ? "active" : "unknown",
      days_remaining: 0,
      expires_at: "",
      cached: false,
    },
    loading: false,
    refetch: vi.fn(),
  };
}

afterEach(cleanup);

describe("ProBadge", () => {
  it("renderiza o rótulo Pro quando a instalação é Free", () => {
    useLicenseStatusMock.mockReturnValue(licenseStatus(false));
    render(<ProBadge />);
    expect(screen.getByText("Pro")).toBeInTheDocument();
  });

  it("continua visível quando a instalação já é Pro (não esconde, só muda estilo)", () => {
    useLicenseStatusMock.mockReturnValue(licenseStatus(true));
    render(<ProBadge />);
    expect(screen.getByText("Pro")).toBeInTheDocument();
  });

  it("erro/borda: enquanto o status ainda carrega, trata como não-Pro (nunca undefined)", () => {
    useLicenseStatusMock.mockReturnValue({
      status: null,
      loading: true,
      refetch: vi.fn(),
    });
    render(<ProBadge />);
    expect(screen.getByText("Pro")).toBeInTheDocument();
  });
});
