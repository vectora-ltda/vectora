// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

vi.mock("next/image", () => ({
  default: (props: Record<string, unknown>) => {
    // eslint-disable-next-line @next/next/no-img-element
    return <img alt={props.alt as string} />;
  },
}));
vi.mock("../contextual-help", () => ({ ContextualHelp: () => null }));
vi.mock("../settings-menu", () => ({ SettingsMenu: () => null }));

const { Header } = await import("../header");
const { m } = await import("@/lib/paraglide/messages");

afterEach(() => {
  cleanup();
  delete (window as { vectora?: unknown }).vectora;
});

describe("Header — marca exibida somente na TitleBar", () => {
  it("não mostra o ícone nem o título Vectora no navegador", () => {
    const { container } = render(<Header />);
    expect(screen.queryByText("Vectora")).not.toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("safe-area-top-header");
  });

  it("também não mostra a marca quando o Electron fornece a TitleBar", async () => {
    window.vectora = {
      windowControls: {
        minimize: vi.fn(),
        maximizeToggle: vi.fn(),
        close: vi.fn(),
        isMaximized: vi.fn().mockResolvedValue(false),
        onStateChange: vi.fn(() => () => undefined),
      },
    } as unknown as Window["vectora"];

    render(<Header />);

    await waitFor(() =>
      expect(screen.queryByText("Vectora")).not.toBeInTheDocument(),
    );
  });
});

describe("Header — navegação compacta", () => {
  it("expõe o controle das sessões e do workbench em tela compacta", () => {
    const onOpenSessions = vi.fn();
    const onToggleWorkbench = vi.fn();
    render(
      <Header
        compactNavigation={{
          onOpenSessions,
          onToggleWorkbench,
          workbenchOpen: false,
        }}
      />,
    );

    screen.getByRole("button", { name: m.mobile_open_sessions() }).click();
    screen.getByRole("button", { name: m.mobile_show_workbench() }).click();

    expect(onOpenSessions).toHaveBeenCalledOnce();
    expect(onToggleWorkbench).toHaveBeenCalledOnce();
  });

  it("oferece voltar ao chat enquanto o workbench está aberto", () => {
    const onToggleWorkbench = vi.fn();
    render(
      <Header
        compactNavigation={{
          onOpenSessions: vi.fn(),
          onToggleWorkbench,
          workbenchOpen: true,
        }}
      />,
    );

    const button = screen.getByRole("button", { name: m.mobile_show_chat() });
    expect(button).toHaveAttribute("aria-pressed", "true");
    button.click();
    expect(onToggleWorkbench).toHaveBeenCalledOnce();
  });

  it("mantém o acesso às sessões quando o modo não oferece workbench no Header", () => {
    const onOpenSessions = vi.fn();
    render(<Header compactNavigation={{ onOpenSessions }} />);

    screen.getByRole("button", { name: m.mobile_open_sessions() }).click();

    expect(onOpenSessions).toHaveBeenCalledOnce();
    expect(
      screen.queryByRole("button", { name: m.mobile_show_workbench() }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: m.mobile_show_chat() }),
    ).not.toBeInTheDocument();
  });

  it("não mostra os controles compactos no layout largo", () => {
    render(<Header />);
    expect(
      screen.queryByRole("button", { name: m.mobile_open_sessions() }),
    ).not.toBeInTheDocument();
  });
});
