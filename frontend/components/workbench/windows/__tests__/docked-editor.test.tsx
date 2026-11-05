// @vitest-environment jsdom
/**
 * DockedEditor — editor ancorado no modo IDE.
 * Empty state quando sem arquivo; tabs quando arquivos abertos.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

const mockCloseDockedTab = vi.fn();
const mockSetDockedActiveTab = vi.fn();
const mockWindowsState = {
  dockedWorkspaceId: null as string | null,
  dockedTabs: [] as string[],
  dockedActiveTab: null as string | null,
  closeDockedTab: mockCloseDockedTab,
  setDockedActiveTab: mockSetDockedActiveTab,
};

vi.mock("@/lib/stores/windows-store", () => ({
  useWindowsStore: (sel: (s: typeof mockWindowsState) => unknown) =>
    sel(mockWindowsState),
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy({}, { get: (_t, prop) => () => String(prop) }),
}));

vi.mock("@/components/workbench/file-editor", () => ({
  FileEditor: ({ path }: { path: string }) => (
    <div data-testid="file-editor" data-path={path} />
  ),
}));

import { DockedEditor } from "../docked-editor";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockWindowsState.dockedWorkspaceId = null;
  mockWindowsState.dockedTabs = [];
  mockWindowsState.dockedActiveTab = null;
});

describe("DockedEditor", () => {
  it("empty state: mostra mensagem quando sem arquivos abertos", () => {
    mockWindowsState.dockedWorkspaceId = null;
    mockWindowsState.dockedTabs = [];
    render(<DockedEditor />);
    expect(screen.getByText("docked_editor_empty")).toBeInTheDocument();
  });

  it("empty state: mostra hint quando sem arquivos abertos", () => {
    mockWindowsState.dockedWorkspaceId = null;
    mockWindowsState.dockedTabs = [];
    render(<DockedEditor />);
    expect(screen.getByText("docked_editor_empty_hint")).toBeInTheDocument();
  });

  it("empty state quando workspaceId é null mas tabs não é vazio (null guard)", () => {
    mockWindowsState.dockedWorkspaceId = null;
    mockWindowsState.dockedTabs = ["/some/file.ts"];
    mockWindowsState.dockedActiveTab = "/some/file.ts";
    render(<DockedEditor />);
    expect(screen.getByText("docked_editor_empty")).toBeInTheDocument();
  });

  it("com uma tab: renderiza o nome do arquivo na tab bar", () => {
    mockWindowsState.dockedWorkspaceId = "ws1";
    mockWindowsState.dockedTabs = ["/project/src/main.ts"];
    mockWindowsState.dockedActiveTab = "/project/src/main.ts";
    render(<DockedEditor />);
    expect(screen.getByText("main.ts")).toBeInTheDocument();
  });

  it("com uma tab: renderiza FileEditor com o path correto", () => {
    mockWindowsState.dockedWorkspaceId = "ws1";
    mockWindowsState.dockedTabs = ["/project/src/main.ts"];
    mockWindowsState.dockedActiveTab = "/project/src/main.ts";
    render(<DockedEditor />);
    const editor = screen.getByTestId("file-editor");
    expect(editor).toHaveAttribute("data-path", "/project/src/main.ts");
  });

  it("múltiplas tabs: todas visíveis na tab bar", () => {
    mockWindowsState.dockedWorkspaceId = "ws1";
    mockWindowsState.dockedTabs = ["/a/foo.ts", "/b/bar.tsx", "/c/baz.py"];
    mockWindowsState.dockedActiveTab = "/a/foo.ts";
    render(<DockedEditor />);
    expect(screen.getByText("foo.ts")).toBeInTheDocument();
    expect(screen.getByText("bar.tsx")).toBeInTheDocument();
    expect(screen.getByText("baz.py")).toBeInTheDocument();
  });

  it("múltiplas tabs: tab ativa tem aria-selected=true", () => {
    mockWindowsState.dockedWorkspaceId = "ws1";
    mockWindowsState.dockedTabs = ["/a/foo.ts", "/b/bar.tsx"];
    mockWindowsState.dockedActiveTab = "/b/bar.tsx";
    render(<DockedEditor />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs[0]).toHaveAttribute("aria-selected", "false");
    expect(tabs[1]).toHaveAttribute("aria-selected", "true");
  });

  it("clicar em tab inativa chama setDockedActiveTab", () => {
    mockWindowsState.dockedWorkspaceId = "ws1";
    mockWindowsState.dockedTabs = ["/a/foo.ts", "/b/bar.tsx"];
    mockWindowsState.dockedActiveTab = "/a/foo.ts";
    render(<DockedEditor />);
    const tabs = screen.getAllByRole("tab");
    fireEvent.click(tabs[1]);
    expect(mockSetDockedActiveTab).toHaveBeenCalledWith("/b/bar.tsx");
  });

  it("clicar no botão fechar de uma tab chama closeDockedTab", () => {
    mockWindowsState.dockedWorkspaceId = "ws1";
    mockWindowsState.dockedTabs = ["/a/foo.ts", "/b/bar.tsx"];
    mockWindowsState.dockedActiveTab = "/a/foo.ts";
    render(<DockedEditor />);
    const closeBtns = screen.getAllByRole("button");
    fireEvent.click(closeBtns[0]);
    expect(mockCloseDockedTab).toHaveBeenCalledWith("/a/foo.ts");
  });

  it("fechar aba não propaga click para setDockedActiveTab", () => {
    mockWindowsState.dockedWorkspaceId = "ws1";
    mockWindowsState.dockedTabs = ["/a/foo.ts"];
    mockWindowsState.dockedActiveTab = "/a/foo.ts";
    render(<DockedEditor />);
    const closeBtn = screen.getByRole("button");
    fireEvent.click(closeBtn);
    expect(mockSetDockedActiveTab).not.toHaveBeenCalled();
  });

  it("defesa em profundidade: dockedWorkspaceId de OUTRO workspace mostra o estado vazio, nunca o arquivo", () => {
    mockWindowsState.dockedWorkspaceId = "ws-snake";
    mockWindowsState.dockedTabs = ["/snake/project.godot"];
    mockWindowsState.dockedActiveTab = "/snake/project.godot";
    render(<DockedEditor activeWorkspaceId="ws-vectora" />);
    expect(screen.getByText("docked_editor_empty")).toBeInTheDocument();
    expect(screen.queryByTestId("file-editor")).not.toBeInTheDocument();
  });

  it("mesmo workspace ativo: renderiza o arquivo normalmente", () => {
    mockWindowsState.dockedWorkspaceId = "ws-vectora";
    mockWindowsState.dockedTabs = ["/vectora/main.py"];
    mockWindowsState.dockedActiveTab = "/vectora/main.py";
    render(<DockedEditor activeWorkspaceId="ws-vectora" />);
    expect(screen.getByTestId("file-editor")).toHaveAttribute(
      "data-path",
      "/vectora/main.py",
    );
  });

  it("sem activeWorkspaceId (prop omitida): não bloqueia — comportamento atual preservado", () => {
    mockWindowsState.dockedWorkspaceId = "ws1";
    mockWindowsState.dockedTabs = ["/project/src/main.ts"];
    mockWindowsState.dockedActiveTab = "/project/src/main.ts";
    render(<DockedEditor />);
    expect(screen.getByTestId("file-editor")).toBeInTheDocument();
  });
});
