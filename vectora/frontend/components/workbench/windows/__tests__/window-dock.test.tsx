// @vitest-environment jsdom
/**
 * WindowDock — barra de janelas minimizadas. Fica oculta quando não há
 * nenhuma janela minimizada, mesmo com janelas abertas e visíveis.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

const mockRestore = vi.fn();
const mockWindowsState = {
  windows: [] as Array<{ id: string; title: string; minimized: boolean }>,
  restore: mockRestore,
};

vi.mock("@/lib/stores/windows-store", () => ({
  useWindowsStore: (sel: (s: typeof mockWindowsState) => unknown) =>
    sel(mockWindowsState),
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_t, prop) => () => String(prop) }),
}));

import { WindowDock } from "../window-dock";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockWindowsState.windows = [];
});

describe("WindowDock", () => {
  it("retorna null quando não há janelas", () => {
    mockWindowsState.windows = [];
    const { container } = render(<WindowDock />);
    expect(container).toBeEmptyDOMElement();
  });

  it("retorna null quando há janelas abertas mas nenhuma minimizada", () => {
    mockWindowsState.windows = [
      { id: "w1", title: "a.ts", minimized: false },
      { id: "w2", title: "b.ts", minimized: false },
    ];
    const { container } = render(<WindowDock />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lista só as janelas minimizadas, ignorando as visíveis", () => {
    mockWindowsState.windows = [
      { id: "w1", title: "aberta.ts", minimized: false },
      { id: "w2", title: "minimizada.ts", minimized: true },
    ];
    const { container } = render(<WindowDock />);
    expect(screen.getByText("minimizada.ts")).toBeInTheDocument();
    expect(screen.queryByText("aberta.ts")).not.toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("safe-area-bottom-dock");
  });

  it("clicar numa janela minimizada chama restore com o id correto", () => {
    mockWindowsState.windows = [
      { id: "w1", title: "foo.ts", minimized: true },
      { id: "w2", title: "bar.ts", minimized: true },
    ];
    render(<WindowDock />);
    fireEvent.click(screen.getByText("bar.ts"));
    expect(mockRestore).toHaveBeenCalledWith("w2");
    expect(mockRestore).not.toHaveBeenCalledWith("w1");
  });
});
