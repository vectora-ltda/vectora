// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { SessionUser } from "#/server/fns/auth";

import Header from "./Header";

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

const { mockSetLocale } = vi.hoisted(() => ({ mockSetLocale: vi.fn() }));

vi.mock("#/paraglide/runtime", () => ({
  getLocale: () => "pt",
  locales: ["pt", "en", "es"],
  setLocale: mockSetLocale,
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

vi.mock("./ThemeToggle", () => ({
  default: () => <button aria-label="theme-toggle-stub" />,
}));

const SESSION: SessionUser = {
  id: "u1",
  email: "a@b.com",
  full_name: "Ana",
  country: "BR",
  language: "pt",
  email_verified: true,
  role: "user",
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Header", () => {
  it("mostra Entrar/Criar Conta quando não há sessão", () => {
    const { container } = render(<Header session={null} />);

    expect(screen.getAllByText("nav_login").length).toBeGreaterThan(0);
    expect(screen.getAllByText("nav_signup").length).toBeGreaterThan(0);
    expect(screen.queryByText("Dashboard")).not.toBeInTheDocument();
    expect(container.querySelector("nav")).toHaveClass("start-1/2");
  });

  it("mostra o link Dashboard quando há sessão (edge — usuário logado)", () => {
    render(<Header session={SESSION} />);

    expect(screen.getAllByText("Dashboard").length).toBeGreaterThan(0);
    expect(screen.queryByText("nav_login")).not.toBeInTheDocument();
  });

  it("abre e fecha o menu mobile ao clicar no hamburger", () => {
    render(<Header session={null} />);
    const toggle = screen.getByLabelText("nav_menu");

    expect(screen.queryByLabelText("Menu mobile")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.getByLabelText("Menu mobile")).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.queryByLabelText("Menu mobile")).not.toBeInTheDocument();
  });

  it("abre o dropdown de idioma e troca de locale ao clicar numa opção", () => {
    render(<Header session={null} />);

    fireEvent.click(screen.getByLabelText("language_label"));
    expect(screen.getByRole("listbox")).toHaveClass("start-1/2");

    fireEvent.click(screen.getByRole("option", { name: "EN" }));
    expect(mockSetLocale).toHaveBeenCalledWith("en");
  });

  it("fecha o dropdown de idioma ao clicar fora (edge)", () => {
    render(
      <div>
        <Header session={null} />
        <div data-testid="outside" />
      </div>,
    );

    fireEvent.click(screen.getByLabelText("language_label"));
    expect(screen.getByRole("listbox")).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByTestId("outside"));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});
