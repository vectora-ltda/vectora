// @vitest-environment jsdom
/**
 * Tela de login (`/auth/signin`) — foco no botão de SSO: só
 * aparece quando `GET /auth/oidc/status` confirma um IDP configurado, e
 * nunca some silenciosamente em erro de rede (fica oculto, sem quebrar o
 * resto da tela).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";

const { navigateSpy } = vi.hoisted(() => ({
  navigateSpy: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (opts: Record<string, unknown>) => ({
    ...opts,
    useSearch: () => ({}),
  }),
  useNavigate: () => navigateSpy,
}));

vi.mock("@/lib/stores/auth-store", () => ({
  useAuthStore: (selector: (s: { setUser: () => void }) => unknown) =>
    selector({ setUser: vi.fn() }),
}));

vi.mock("@/lib/utils/return-to", () => ({
  consumeReturnTo: () => null,
}));

import type { ReactElement } from "react";
import { Route } from "../signin";

const SignInPage = (Route as unknown as { component: () => ReactElement })
  .component;

function mockFetchSequence(responses: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const path = url.toString();
      const body = Object.entries(responses).find(([key]) =>
        path.includes(key),
      )?.[1] ?? { exists: true };
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }),
  );
}

beforeEach(() => {
  navigateSpy.mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SignInPage — botão de SSO", () => {
  it("aparece com link pro handshake quando o backend confirma IDP configurado", async () => {
    mockFetchSequence({
      "/auth/has-users": { exists: true },
      "/auth/oidc/status": { enabled: true },
    });

    render(<SignInPage />);

    const ssoLink = await screen.findByRole("link", { name: /sso/i });
    expect(ssoLink).toHaveAttribute("href", "/auth/oidc/login");
  });

  it("erro/borda: sem IDP configurado (ou falha de rede) o botão nunca aparece", async () => {
    mockFetchSequence({
      "/auth/has-users": { exists: true },
      "/auth/oidc/status": { enabled: false },
    });

    render(<SignInPage />);

    // Espera o formulário de login local renderizar normalmente antes de
    // confirmar a ausência do link — evita falso-positivo por render cedo
    // demais.
    await screen.findByText(/sign in/i, { selector: "button" });
    expect(screen.queryByRole("link", { name: /sso/i })).toBeNull();
  });

  it("falha de rede no status de SSO não quebra a tela de login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url.toString().includes("/auth/oidc/status")) {
          return Promise.reject(new Error("network down"));
        }
        return Promise.resolve(
          new Response(JSON.stringify({ exists: true }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }),
    );

    render(<SignInPage />);

    await screen.findByText(/sign in/i, { selector: "button" });
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: /sso/i })).toBeNull(),
    );
  });
});
