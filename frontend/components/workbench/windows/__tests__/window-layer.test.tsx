// @vitest-environment jsdom
/**
 * WindowLayer — overlay de janelas flutuantes.
 * Retorna null no modo IDE ou quando não há janelas visíveis.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";

const mockSettings = { uiMode: "assistant" };
const mockWindowsState = {
  windows: [] as Array<{ id: string; minimized: boolean }>,
};

vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (sel: (s: typeof mockSettings) => unknown) =>
    sel(mockSettings),
}));

vi.mock("@/lib/stores/windows-store", () => ({
  useWindowsStore: (
    sel: (s: { windows: typeof mockWindowsState.windows }) => unknown,
  ) => sel(mockWindowsState),
}));

vi.mock("../file-window", () => ({
  FileWindow: ({ win }: { win: { id: string } }) => (
    <div data-testid="file-window" data-id={win.id} />
  ),
}));

import { WindowLayer } from "../window-layer";

afterEach(() => {
  cleanup();
  mockSettings.uiMode = "assistant";
  mockWindowsState.windows = [];
});

describe("WindowLayer", () => {
  it("retorna null quando uiMode='ide'", () => {
    mockSettings.uiMode = "ide";
    mockWindowsState.windows = [{ id: "ws1", minimized: false }];
    const { container } = render(<WindowLayer />);
    expect(container).toBeEmptyDOMElement();
  });

  it("retorna null quando não há janelas", () => {
    mockSettings.uiMode = "assistant";
    mockWindowsState.windows = [];
    const { container } = render(<WindowLayer />);
    expect(container).toBeEmptyDOMElement();
  });

  it("retorna null quando todas as janelas estão minimizadas", () => {
    mockSettings.uiMode = "assistant";
    mockWindowsState.windows = [{ id: "ws1", minimized: true }];
    const { container } = render(<WindowLayer />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renderiza FileWindow para cada janela visível (não minimizada)", () => {
    mockSettings.uiMode = "assistant";
    mockWindowsState.windows = [
      { id: "ws1", minimized: false },
      { id: "ws2", minimized: false },
      { id: "ws3", minimized: true },
    ];
    const { getAllByTestId } = render(<WindowLayer />);
    expect(getAllByTestId("file-window")).toHaveLength(2);
  });

  it("renderiza FileWindow com o id correto", () => {
    mockSettings.uiMode = "assistant";
    mockWindowsState.windows = [{ id: "ws42", minimized: false }];
    const { getByTestId } = render(<WindowLayer />);
    expect(getByTestId("file-window")).toHaveAttribute("data-id", "ws42");
  });

  it("uiMode='ide' retorna null mesmo com janelas visíveis (edge: ambas condições)", () => {
    mockSettings.uiMode = "ide";
    mockWindowsState.windows = [
      { id: "ws1", minimized: false },
      { id: "ws2", minimized: false },
    ];
    const { container } = render(<WindowLayer />);
    expect(container).toBeEmptyDOMElement();
  });

  it("uiMode='kanban' retorna null mesmo com janelas visíveis", () => {
    // Regra: a camada só monta no modo Assistente. Um terceiro modo não
    // pode cair no ramo "renderiza" por omissão — senão o board Kanban
    // ganharia janelas flutuantes sobrepostas sem ninguém ter pedido.
    mockSettings.uiMode = "kanban";
    mockWindowsState.windows = [{ id: "ws1", minimized: false }];
    const { container } = render(<WindowLayer />);
    expect(container).toBeEmptyDOMElement();
  });
});
