// @vitest-environment jsdom
/**
 * FileWindow — janela flutuante com abas. Rnd é mockado como wrapper burro
 * (drag/resize são responsabilidade da lib, não deste componente) que expõe
 * onDragStop/onResizeStop/onMouseDown via botões de teste.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import type { ReactNode } from "react";

const mockFocus = vi.fn();
const mockClose = vi.fn();
const mockCloseTab = vi.fn();
const mockMinimize = vi.fn();
const mockSetActiveTab = vi.fn();
const mockSetBounds = vi.fn();

const mockWindowsState = {
  focus: mockFocus,
  close: mockClose,
  closeTab: mockCloseTab,
  minimize: mockMinimize,
  setActiveTab: mockSetActiveTab,
  setBounds: mockSetBounds,
};

vi.mock("@/lib/stores/windows-store", () => ({
  useWindowsStore: (sel: (s: typeof mockWindowsState) => unknown) =>
    sel(mockWindowsState),
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_t, prop) => () => String(prop) }),
}));

vi.mock("@/components/workbench/file-editor", () => ({
  FileEditor: ({
    workspaceId,
    path,
  }: {
    workspaceId: string;
    path: string;
  }) => (
    <div
      data-testid="file-editor"
      data-workspace-id={workspaceId}
      data-path={path}
    />
  ),
}));

vi.mock("react-rnd", () => ({
  Rnd: ({
    children,
    onMouseDown,
    onDragStop,
    onResizeStop,
  }: {
    children: ReactNode;
    onMouseDown: () => void;
    onDragStop: (e: unknown, d: { x: number; y: number }) => void;
    onResizeStop: (
      e: unknown,
      dir: unknown,
      ref: { offsetWidth: number; offsetHeight: number },
      delta: unknown,
      pos: { x: number; y: number },
    ) => void;
  }) => (
    <div data-testid="rnd" onMouseDown={onMouseDown}>
      <button
        data-testid="trigger-drag-stop"
        onClick={() => onDragStop({}, { x: 42, y: 7 })}
      />
      <button
        data-testid="trigger-resize-stop"
        onClick={() =>
          onResizeStop(
            {},
            "",
            { offsetWidth: 500, offsetHeight: 300 },
            {},
            { x: 10, y: 20 },
          )
        }
      />
      {children}
    </div>
  ),
}));

import { FileWindow } from "../file-window";
import type { FileWindowState } from "@/lib/stores/windows-store";

function makeWin(overrides: Partial<FileWindowState> = {}): FileWindowState {
  return {
    id: "win1",
    workspaceId: "ws1",
    tabs: ["/project/src/main.ts"],
    activeTab: "/project/src/main.ts",
    title: "main.ts",
    x: 0,
    y: 0,
    w: 400,
    h: 300,
    minimized: false,
    zIndex: 1,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("FileWindow", () => {
  it("renderiza o título da janela na barra de título", () => {
    render(<FileWindow win={makeWin({ title: "arquivo.py" })} />);
    expect(screen.getByText("arquivo.py")).toBeInTheDocument();
  });

  it("renderiza FileEditor com workspaceId e path da aba ativa", () => {
    render(
      <FileWindow
        win={makeWin({ workspaceId: "ws42", activeTab: "/a/b.ts" })}
      />,
    );
    const editor = screen.getByTestId("file-editor");
    expect(editor).toHaveAttribute("data-workspace-id", "ws42");
    expect(editor).toHaveAttribute("data-path", "/a/b.ts");
  });

  it("com uma única aba: barra de abas não aparece (só o X da janela existe)", () => {
    render(<FileWindow win={makeWin({ tabs: ["/a/b.ts"] })} />);
    expect(screen.getAllByLabelText("window_close")).toHaveLength(1);
  });

  it("com múltiplas abas: barra de abas mostra o basename de cada uma", () => {
    render(
      <FileWindow
        win={makeWin({
          tabs: ["/a/foo.ts", "/b/bar.tsx"],
          activeTab: "/a/foo.ts",
        })}
      />,
    );
    expect(screen.getByText("foo.ts")).toBeInTheDocument();
    expect(screen.getByText("bar.tsx")).toBeInTheDocument();
  });

  it("clicar numa aba inativa chama setActiveTab com o id e path corretos", () => {
    render(
      <FileWindow
        win={makeWin({
          id: "win7",
          tabs: ["/a/foo.ts", "/b/bar.tsx"],
          activeTab: "/a/foo.ts",
        })}
      />,
    );
    fireEvent.click(screen.getByText("bar.tsx"));
    expect(mockSetActiveTab).toHaveBeenCalledWith("win7", "/b/bar.tsx");
  });

  it("clicar no X de uma aba chama closeTab e não propaga para setActiveTab", () => {
    render(
      <FileWindow
        win={makeWin({
          id: "win7",
          tabs: ["/a/foo.ts", "/b/bar.tsx"],
          activeTab: "/a/foo.ts",
        })}
      />,
    );
    // índice 0 é o X da barra de título (fecha a janela); os X's das abas vêm depois.
    const closeButtons = screen.getAllByLabelText("window_close");
    fireEvent.click(closeButtons[1]);
    expect(mockCloseTab).toHaveBeenCalledWith("win7", "/a/foo.ts");
    expect(mockSetActiveTab).not.toHaveBeenCalled();
    expect(mockClose).not.toHaveBeenCalled();
  });

  it("clicar em minimizar chama minimize(win.id)", () => {
    render(<FileWindow win={makeWin({ id: "win9" })} />);
    fireEvent.click(screen.getByLabelText("window_minimize"));
    expect(mockMinimize).toHaveBeenCalledWith("win9");
  });

  it("clicar em fechar a janela (barra de título) chama close(win.id)", () => {
    render(<FileWindow win={makeWin({ id: "win9", tabs: ["/a.ts"] })} />);
    fireEvent.click(screen.getByLabelText("window_close"));
    expect(mockClose).toHaveBeenCalledWith("win9");
  });

  it("mousedown na janela chama focus(win.id)", () => {
    render(<FileWindow win={makeWin({ id: "win5" })} />);
    fireEvent.mouseDown(screen.getByTestId("rnd"));
    expect(mockFocus).toHaveBeenCalledWith("win5");
  });

  it("parar de arrastar chama setBounds só com x/y", () => {
    render(<FileWindow win={makeWin({ id: "win5" })} />);
    fireEvent.click(screen.getByTestId("trigger-drag-stop"));
    expect(mockSetBounds).toHaveBeenCalledWith("win5", { x: 42, y: 7 });
  });

  it("parar de redimensionar chama setBounds com w/h/x/y", () => {
    render(<FileWindow win={makeWin({ id: "win5" })} />);
    fireEvent.click(screen.getByTestId("trigger-resize-stop"));
    expect(mockSetBounds).toHaveBeenCalledWith("win5", {
      w: 500,
      h: 300,
      x: 10,
      y: 20,
    });
  });
});
