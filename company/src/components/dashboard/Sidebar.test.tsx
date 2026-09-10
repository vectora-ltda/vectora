// @vitest-environment jsdom
/**
 * Tests para Sidebar: link Admin só pra role=admin, e botão de logout
 * chamando signOut() + redirecionando pra /login.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import Sidebar from "./Sidebar";

const mockSignOut = vi.fn();
let mockSession: { role?: string } | null = null;

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
  useRouterState: () => ({ location: { pathname: "/dashboard" } }),
  useRouteContext: () => ({ session: mockSession }),
}));

vi.mock("#/server/fns/auth", () => ({
  signOut: () => mockSignOut(),
}));

vi.mock("#/components/shared/ThemeToggle", () => ({
  default: () => <button aria-label="theme-toggle-stub" />,
}));

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

const originalLocation = window.location;

beforeEach(() => {
  vi.clearAllMocks();
  mockSession = { role: "user" };
  mockSignOut.mockResolvedValue({ ok: true });
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...originalLocation, href: "" },
  });
});

afterEach(() => {
  cleanup();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
});

describe("Sidebar", () => {
  it("não mostra o link Admin para usuário comum", () => {
    const { container } = render(<Sidebar />);
    expect(screen.queryByText("nav_admin")).not.toBeInTheDocument();
    expect(container.querySelector("aside")).toHaveClass("border-s");
  });

  it("mostra o link Admin quando role=admin", () => {
    mockSession = { role: "admin" };
    render(<Sidebar />);
    expect(screen.getByText("nav_admin")).toBeInTheDocument();
  });

  it("clicar em Sair chama signOut() e redireciona pra /login", async () => {
    render(<Sidebar />);

    fireEvent.click(screen.getByText("nav_logout"));

    await vi.waitFor(() => expect(mockSignOut).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(window.location.href).toBe("/login"));
  });
});
