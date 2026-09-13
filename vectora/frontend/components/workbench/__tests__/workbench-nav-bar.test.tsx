// @vitest-environment jsdom
/**
 * WorkbenchNavBar — renderização e navegação de abas.
 *
 * Cobre: NavTabButton para todas as abas e a chamada de selectTab ao clicar.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

// ── mocks ────────────────────────────────────────────────────────────────────

const mockPush = vi.fn();
vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: { getState: () => ({ push: mockPush }) },
}));

const mockSelectTab = vi.fn();
const mockSetPanelOpen = vi.fn();
let mockIsOpen = true;
vi.mock("@/lib/stores/workbench-store", () => ({
  WORKBENCH_TABS: ["files", "context_graph", "terminal"],
  useWorkbenchStore: (sel: (s: object) => unknown) =>
    sel({
      getActiveTab: () => "files",
      isOpen: () => mockIsOpen,
      selectTab: mockSelectTab,
      setPanelOpen: mockSetPanelOpen,
      list: () => [],
      pinnedFiles: {},
      getPlan: () => ({ items: [] }),
      pending: {},
    }),
}));

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (sel: (s: object) => unknown) =>
    sel({ getActive: () => null }),
}));

vi.mock("@/lib/hooks/use-hydrated", () => ({ useHydrated: () => true }));
vi.mock("@/lib/hooks/use-workspace-watcher", () => ({
  useWorkspaceWatcher: () => undefined,
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get: (_t, prop) => () => String(prop),
    },
  ),
}));
vi.mock("@/lib/i18n-dyn", () => ({
  mDyn: (key: string) => key,
}));
vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({
    children,
    asChild,
  }: {
    children: React.ReactNode;
    asChild?: boolean;
  }) => {
    void asChild;
    return <>{children}</>;
  },
  TooltipContent: ({ children }: { children: React.ReactNode }) => (
    <span data-tooltip>{children}</span>
  ),
}));

import { WorkbenchNavBar, WorkbenchContent } from "../workbench-panel";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockIsOpen = true;
});

// ── helpers ──────────────────────────────────────────────────────────────────

function renderNav() {
  return render(<WorkbenchNavBar threadId="t1" />);
}

// Localiza o botão pelo ícone SVG que está dentro dele (via data-testid do lucide).
// Como não temos data-testid nos botões, pegamos pelo índice da lista.
function getButtons(container: HTMLElement) {
  return Array.from(
    container.querySelectorAll('[data-testid^="workbench-nav-"]'),
  ) as HTMLButtonElement[];
}

// ── Testes ───────────────────────────────────────────────────────────────────

describe("WorkbenchNavBar — Todas as abas estáveis", () => {
  it("alterna a rail e reabre a última tab preservada", () => {
    const view = renderNav();
    const toggle = screen.getByTestId("workbench-collapse");
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(toggle);
    expect(mockSetPanelOpen).toHaveBeenCalledWith("t1", false);

    mockIsOpen = false;
    view.rerender(<WorkbenchNavBar threadId="t1" />);
    const collapsedToggle = screen.getByTestId("workbench-collapse");
    expect(collapsedToggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(collapsedToggle);
    expect(mockSetPanelOpen).toHaveBeenLastCalledWith("t1", true);
  });

  it("context_graph renderiza como NavTabButton normal (sem aria-disabled)", () => {
    const { container } = renderNav();
    const btns = getButtons(container);
    // 3 tabs no mock: files (0), context_graph (1), terminal (2)
    expect(btns[1].hasAttribute("aria-disabled")).toBe(false);
  });

  it("context_graph fica na ordem de tab normal (sem tabIndex=-1)", () => {
    const { container } = renderNav();
    expect(getButtons(container)[1].tabIndex).not.toBe(-1);
  });

  it("clicar em context_graph chama selectTab normalmente", () => {
    const { container } = renderNav();
    fireEvent.click(getButtons(container)[1]);
    expect(mockSelectTab).toHaveBeenCalledWith("t1", "context_graph");
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("clicar em tab normal (files) chama selectTab e NÃO despacha toast", () => {
    const { container } = renderNav();
    fireEvent.click(getButtons(container)[0]);
    expect(mockSelectTab).toHaveBeenCalledWith("t1", "files");
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("tab normal (terminal) chama selectTab", () => {
    const { container } = renderNav();
    fireEvent.click(getButtons(container)[2]);
    expect(mockSelectTab).toHaveBeenCalledWith("t1", "terminal");
    expect(mockPush).not.toHaveBeenCalled();
  });
});

describe("WorkbenchNavBar — prop side (layout IDE vs Assistente)", () => {
  it('side="left" aplica border-r na raiz (IDE: workbench à esquerda do editor)', () => {
    const { container } = render(<WorkbenchNavBar threadId="t1" side="left" />);
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("border-r");
    expect(root.className).not.toContain("border-l");
  });

  it('side="right" (padrão) aplica border-l na raiz (Assistente: workbench à direita)', () => {
    const { container } = render(
      <WorkbenchNavBar threadId="t1" side="right" />,
    );
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("border-l");
    expect(root.className).not.toContain("border-r");
  });

  it("sem prop side usa border-l por padrão", () => {
    const { container } = render(<WorkbenchNavBar threadId="t1" />);
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("border-l");
  });

  it('side="right" renderiza spacer h-16 (alinha com o Header no layout Assistente)', () => {
    const { container } = render(
      <WorkbenchNavBar threadId="t1" side="right" />,
    );
    expect(screen.getByTestId("workbench-header-spacer")).toBeInTheDocument();
  });

  it('side="left" TAMBÉM renderiza spacer h-16 (WorkbenchContent sempre tem header h-16, independente do side, pra manter os ícones da NavBar alinhados no modo IDE)', () => {
    const { container } = render(<WorkbenchNavBar threadId="t1" side="left" />);
    expect(screen.getByTestId("workbench-header-spacer")).toBeInTheDocument();
  });
});

vi.mock("@/components/workbench/terminal/terminal-panel", () => ({
  TerminalPanel: () => null,
}));
vi.mock("@/components/workbench/files/files-tab", () => ({
  FilesTab: () => null,
}));
vi.mock("@/components/workbench/git/git-tab", () => ({
  GitTab: () => null,
}));
vi.mock("@/components/workbench/tabs/plan-tab", () => ({
  PlanTab: () => null,
}));
vi.mock("@/components/workbench/tabs/browser-tab", () => ({
  BrowserTab: () => null,
}));
vi.mock("@/components/workbench/tabs/memory-tab", () => ({
  MemoryTab: () => null,
}));
vi.mock("@/components/workbench/tabs/tasks-tab", () => ({
  TasksTab: () => null,
}));

describe("WorkbenchContent — prop side (layout IDE vs Assistente)", () => {
  it('side="left" aplica border-r na raiz', () => {
    const { container } = render(
      <WorkbenchContent threadId="t1" side="left" />,
    );
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("border-r");
    expect(root.className).not.toContain("border-l");
  });

  it('side="right" (padrão) aplica border-l na raiz', () => {
    const { container } = render(
      <WorkbenchContent threadId="t1" side="right" />,
    );
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("border-l");
    expect(root.className).not.toContain("border-r");
  });

  it("sem prop side usa border-l por padrão", () => {
    const { container } = render(<WorkbenchContent threadId="t1" />);
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("border-l");
  });
});
