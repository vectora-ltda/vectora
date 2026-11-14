// @vitest-environment jsdom
/**
 * Página /integrations — a aba de integrações em página inteira, com o caminho
 * de volta ao app.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: () => React.ReactNode }) => ({
    options,
  }),
  Link: ({
    to,
    children,
    ...rest
  }: {
    to: string;
    children: React.ReactNode;
  }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/settings/environment/tabs/integracoes-tab", () => ({
  IntegracoesTab: (props: { onOpenFullPage?: unknown }) => (
    <div
      data-testid="tab"
      data-has-shortcut={String(Boolean(props.onOpenFullPage))}
    />
  ),
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_target, key) => () => String(key) }),
}));

afterEach(cleanup);

async function renderPage() {
  const { Route } = (await import("../integrations/index")) as unknown as {
    Route: { options: { component: () => React.ReactNode } };
  };
  const Page = Route.options.component;
  render(<Page />);
}

describe("página /integrations", () => {
  it("mostra o título e a aba de integrações, sem o atalho de página inteira", async () => {
    await renderPage();

    expect(screen.getByText("integrations_page_title")).toBeTruthy();
    expect(
      (await screen.findByTestId("tab")).getAttribute("data-has-shortcut"),
    ).toBe("false");
  });

  it("tem um caminho de volta ao app", async () => {
    await renderPage();

    const back = screen.getByText("integrations_page_back").closest("a");
    expect(back?.getAttribute("href")).toBe("/");
  });
});
