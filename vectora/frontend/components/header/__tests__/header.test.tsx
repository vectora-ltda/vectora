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

afterEach(() => {
  cleanup();
  delete (window as { vectora?: unknown }).vectora;
});

describe("Header — ícone/título duplicado no desktop", () => {
  it("mostra o ícone e o título Vectora fora do desktop (browser puro)", () => {
    const { container } = render(<Header />);
    expect(screen.getByText("Vectora")).toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("safe-area-top-header");
  });

  it("esconde o ícone e o título quando window.vectora existe (já aparecem na TitleBar)", async () => {
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

    await waitFor(() => {
      expect(screen.queryByText("Vectora")).not.toBeInTheDocument();
    });
  });
});
