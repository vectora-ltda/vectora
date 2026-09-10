// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import CookieConsent from "./CookieConsent";

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

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    to,
    ...rest
  }: {
    children: React.ReactNode;
    to: string;
  } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

const STORAGE_KEY = "cookie-consent";

beforeEach(() => {
  localStorage.clear();
  delete (window as { gtag?: unknown }).gtag;
});

describe("CookieConsent", () => {
  it("aparece quando o consentimento ainda não foi decidido", () => {
    render(<CookieConsent />);
    expect(screen.getByRole("dialog")).toHaveClass("start-0", "end-0");
  });

  it("não aparece quando o consentimento já foi salvo (edge — visita recorrente)", () => {
    localStorage.setItem(STORAGE_KEY, "accepted");
    render(<CookieConsent />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("aceitar persiste 'accepted', esconde o banner e concede consent no gtag", () => {
    const gtag = vi.fn();
    window.gtag = gtag;
    render(<CookieConsent />);

    fireEvent.click(screen.getByText("cookie_accept"));

    expect(localStorage.getItem(STORAGE_KEY)).toBe("accepted");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(gtag).toHaveBeenCalledWith(
      "consent",
      "update",
      expect.objectContaining({ analytics_storage: "granted" }),
    );
  });

  it("rejeitar persiste 'rejected', esconde o banner e nega consent no gtag", () => {
    const gtag = vi.fn();
    window.gtag = gtag;
    render(<CookieConsent />);

    fireEvent.click(screen.getByText("cookie_reject"));

    expect(localStorage.getItem(STORAGE_KEY)).toBe("rejected");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(gtag).toHaveBeenCalledWith(
      "consent",
      "update",
      expect.objectContaining({ analytics_storage: "denied" }),
    );
  });

  it("não lança quando window.gtag está ausente (edge — bloqueador de ads)", () => {
    render(<CookieConsent />);
    expect(() =>
      fireEvent.click(screen.getByText("cookie_accept")),
    ).not.toThrow();
  });
});
