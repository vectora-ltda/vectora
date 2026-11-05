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
