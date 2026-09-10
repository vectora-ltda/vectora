// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import AuthLayout from "./AuthLayout";

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

describe("AuthLayout", () => {
  it("renderiza o heading e os children", () => {
    render(
      <AuthLayout heading="Entrar">
        <input aria-label="email" />
      </AuthLayout>,
    );

    expect(screen.getByRole("heading", { name: "Entrar" })).toBeInTheDocument();
    expect(screen.getByLabelText("email")).toBeInTheDocument();
  });

  it("renderiza o subheading quando informado", () => {
    render(
      <AuthLayout heading="Entrar" subheading={<p>Bem-vindo de volta</p>}>
        <div />
      </AuthLayout>,
    );

    expect(screen.getByText("Bem-vindo de volta")).toBeInTheDocument();
  });

  it("não renderiza subheading quando omitido (edge)", () => {
    render(
      <AuthLayout heading="Entrar">
        <div />
      </AuthLayout>,
    );

    expect(screen.queryByText("Bem-vindo de volta")).not.toBeInTheDocument();
  });

  it("sempre tem um link 'voltar' para a home", () => {
    render(
      <AuthLayout heading="Entrar">
        <div />
      </AuthLayout>,
    );

    expect(screen.getByRole("link", { name: "nav_back" })).toHaveAttribute(
      "href",
      "/",
    );
  });

  it("posiciona o link de retorno com insets lógicos", () => {
    render(
      <AuthLayout heading="Entrar">
        <div />
      </AuthLayout>,
    );
    expect(screen.getByRole("link", { name: "nav_back" })).toHaveClass(
      "start-4",
      "sm:start-6",
    );
  });
});
