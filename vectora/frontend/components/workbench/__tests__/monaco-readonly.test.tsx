// @vitest-environment jsdom
import { describe, expect, it, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

vi.mock("@monaco-editor/react", () => ({
  default: ({
    value,
    language,
    theme,
    options,
  }: {
    value?: string;
    language?: string;
    theme?: string;
    options?: { readOnly?: boolean; domReadOnly?: boolean; fontSize?: number };
  }) => (
    <div
      data-testid="monaco-editor"
      data-language={language}
      data-theme={theme}
      data-readonly={String(!!options?.readOnly)}
      data-dom-readonly={String(!!options?.domReadOnly)}
      data-font-size={options?.fontSize}
    >
      {value}
    </div>
  ),
}));

vi.mock("@/lib/monaco/setup", () => ({
  languageFromPath: (path: string) =>
    path.endsWith(".py") ? "python" : "typescript",
}));

const mockSettings = { monacoFontSize: 13 };
vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (sel: (s: typeof mockSettings) => unknown) =>
    sel(mockSettings),
}));

import MonacoReadOnly from "../monaco-readonly";

afterEach(() => {
  cleanup();
  mockSettings.monacoFontSize = 13;
});

describe("MonacoReadOnly", () => {
  it("renderiza o conteúdo recebido e a linguagem derivada do path", () => {
    render(<MonacoReadOnly value="print(1)" path="script.py" isDark={false} />);
    const editor = screen.getByTestId("monaco-editor");
    expect(editor.textContent).toBe("print(1)");
    expect(editor.getAttribute("data-language")).toBe("python");
  });

  it("options.readOnly e domReadOnly sempre true — nunca editável", () => {
    render(<MonacoReadOnly value="x" path="a.ts" isDark={false} />);
    const editor = screen.getByTestId("monaco-editor");
    expect(editor.getAttribute("data-readonly")).toBe("true");
    expect(editor.getAttribute("data-dom-readonly")).toBe("true");
  });

  it("aplica `monacoFontSize` do settings-store nas options", () => {
    mockSettings.monacoFontSize = 21;
    render(<MonacoReadOnly value="x" path="a.ts" isDark={false} />);
    expect(
      screen.getByTestId("monaco-editor").getAttribute("data-font-size"),
    ).toBe("21");
  });

  it("isDark controla o tema Godot claro/escuro", () => {
    const { rerender } = render(
      <MonacoReadOnly value="x" path="a.ts" isDark={true} />,
    );
    expect(screen.getByTestId("monaco-editor").getAttribute("data-theme")).toBe(
      "vectora-godot-dark",
    );
    rerender(<MonacoReadOnly value="x" path="a.ts" isDark={false} />);
    expect(screen.getByTestId("monaco-editor").getAttribute("data-theme")).toBe(
      "vectora-godot-light",
    );
  });

  it("conteúdo vazio (value='') não lança e renderiza editor sem texto", () => {
    expect(() =>
      render(<MonacoReadOnly value="" path="vazio.ts" isDark={false} />),
    ).not.toThrow();
    expect(screen.getByTestId("monaco-editor").textContent).toBe("");
  });
});
