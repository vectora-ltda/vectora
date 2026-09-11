// @vitest-environment jsdom

import { describe, expect, it, afterEach, vi } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  act,
} from "@testing-library/react";
import { ThreadGroup } from "../thread-group";
import { WorkspaceGroup } from "../workspace-group";
import { SidebarFooter } from "../sidebar-footer";
import { submitFeedback } from "@/lib/api/vectora-client";
import type { Thread } from "@/lib/hooks/threads";
import type { WorkspaceInfo } from "@/lib/stores/workspaces-store";

vi.mock("@/lib/stores/streaming-store", () => ({
  useStreamingStore: () => null,
}));

vi.mock("@/lib/stores/settings-store", () => ({
  useSettingsStore: (sel: (s: unknown) => unknown) =>
    sel({ sidebarWidth: 0, theme: "light" }),
}));

vi.mock("@/lib/hooks/use-hydrated", () => ({
  useHydrated: () => false,
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: {
    sidebar_new_conversation: () => "Nova conversa",
    sidebar_documentation: () => "Documentação",
    sidebar_documentation_caption: () => "Saiba mais",
    sidebar_feedback: () => "Feedback",
    feedback_title: () => "Enviar feedback",
    feedback_bug: () => "Bug",
    feedback_suggestion: () => "Sugestão",
    feedback_cancel: () => "Cancelar",
    feedback_send: () => "Enviar",
    feedback_required: () => "Descreva o problema",
    feedback_sent: () => "Enviado",
    feedback_rate_limited: () => "Limite atingido",
    feedback_error: () => "Erro",
    feedback_include_context: () => "Incluir contexto técnico",
    sidebar_docs: () => "Docs",
    sidebar_report_issue: () => "Reportar problema",
    sidebar_workspace_collapse: () => "Recolher",
    sidebar_workspace_expand: () => "Expandir",
    sidebar_workspace_thread_count: ({ n }: { n: number }) => `${n}`,
    sidebar_delete_thread: () => "Excluir conversa",
    sidebar_ctx_rename: () => "Renomear",
    sidebar_ctx_pin: () => "Fixar",
    sidebar_ctx_unpin: () => "Desafixar",
    sidebar_ctx_delete: () => "Apagar",
    sidebar_rename_placeholder: () => "Nome da sessão",
    workbench_close: () => "Fechar",
  },
}));

vi.mock("../../src/router", () => ({
  queryClient: { prefetchQuery: vi.fn() },
}));
vi.mock("@/lib/api/vectora-client", () => ({
  getHistory: vi.fn(),
  listThreads: vi.fn(),
  submitFeedback: vi.fn().mockResolvedValue({ id: "feedback-1" }),
}));
vi.mock("@/lib/queries/threads", () => ({
  threadsQueryKey: (limit = 100) => ["threads", limit],
}));
vi.mock("./sidebar-utils", () => ({
  shortWorkspaceName: (ws: WorkspaceInfo) => ws.name ?? ws.id,
}));

// Corta a cadeia transitiva até o docked editor (file-editor.tsx importa
// @monaco-editor/react + lib/monaco/setup, que registra web workers reais
// via imports `?worker` — sem sentido fora de um browser real).
vi.mock("@monaco-editor/react", () => ({ default: () => null }));
vi.mock("@/lib/monaco/setup", () => ({
  languageFromPath: () => "plaintext",
}));

afterEach(() => {
  cleanup();
  vi.mocked(submitFeedback).mockReset();
  vi.mocked(submitFeedback).mockResolvedValue({ id: "feedback-1" });
});

function makeThread(id: string): Thread {
  return {
    thread_id: id,
    metadata: { title: `Conversa ${id}` },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  } as unknown as Thread;
}

function makeWorkspace(id: string): WorkspaceInfo {
  return { id, name: `Workspace ${id}`, cwd: `/tmp/${id}` } as WorkspaceInfo;
}

const noop = vi.fn();

describe("ThreadGroup — layout compacto", () => {
  it("renderiza threads e usa mt-2 first:mt-0 no container", () => {
    const threads = [makeThread("a"), makeThread("b")];
    const { container } = render(
      <ThreadGroup
        threads={threads}
        label="Hoje"
        currentThreadId=""
        onSelect={noop}
        onDelete={noop}
        onRename={noop}
        onTogglePin={noop}
      />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain("mt-2");
    expect(root.className).toContain("first:mt-0");
  });

  it("label do grupo usa mb-0 (sem espaço extra abaixo)", () => {
    const { container } = render(
      <ThreadGroup
        threads={[makeThread("x")]}
        label="Hoje"
        currentThreadId=""
        onSelect={noop}
        onDelete={noop}
        onRename={noop}
        onTogglePin={noop}
      />,
    );
    const h3 = container.querySelector("h3")!;
    expect(h3.className).toContain("mb-0");
    expect(h3.className).not.toContain("mb-0.5");
  });

  it("retorna null com lista vazia", () => {
    const { container } = render(
      <ThreadGroup
        threads={[]}
        label="Hoje"
        currentThreadId=""
        onSelect={noop}
        onDelete={noop}
        onRename={noop}
        onTogglePin={noop}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("cada ThreadItem usa py-1 (não py-1.5)", () => {
    const { container } = render(
      <ThreadGroup
        threads={[makeThread("a")]}
        label="Hoje"
        currentThreadId=""
        onSelect={noop}
        onDelete={noop}
        onRename={noop}
        onTogglePin={noop}
      />,
    );
    const item = container.querySelector("[class*='py-1']")!;
    expect(item).toBeTruthy();
    expect(item.className).not.toMatch(/\bpy-1\.5\b/);
  });

  it("título do thread usa text-[12px] (não text-[13px])", () => {
    render(
      <ThreadGroup
        threads={[makeThread("z")]}
        label="Hoje"
        currentThreadId=""
        onSelect={noop}
        onDelete={noop}
        onRename={noop}
        onTogglePin={noop}
      />,
    );
    const span = screen.getByText("Conversa z");
    expect(span.className).toContain("text-[12px]");
    expect(span.className).not.toContain("text-[13px]");
  });
});

describe("WorkspaceGroup — layout compacto", () => {
  it("header usa py-0.5 mb-1 (reduzido de py-1 mb-2)", () => {
    const ws = makeWorkspace("ws1");
    const { container } = render(
      <WorkspaceGroup
        workspace={ws}
        threads={[makeThread("t1")]}
        isSearching={false}
        isCollapsed={false}
        currentThreadId=""
        onToggle={noop}
        onSelect={noop}
        onDelete={noop}
        onRename={noop}
        onTogglePin={noop}
      />,
    );
    const btn = container.querySelector("button")!;
    expect(btn.className).toContain("py-0.5");
    expect(btn.className).toContain("mb-1");
    expect(btn.className).not.toContain("py-1 ");
    expect(btn.className).not.toContain("mb-2");
  });

  it("lista de threads usa space-y-0.5 (não space-y-2)", () => {
    const ws = makeWorkspace("ws2");
    const { container } = render(
      <WorkspaceGroup
        workspace={ws}
        threads={[makeThread("t1"), makeThread("t2")]}
        isSearching={false}
        isCollapsed={false}
        currentThreadId=""
        onToggle={noop}
        onSelect={noop}
        onDelete={noop}
        onRename={noop}
        onTogglePin={noop}
      />,
    );
    const list = container.querySelector("[class*='space-y']")!;
    expect(list.className).toContain("space-y-0.5");
    expect(list.className).not.toContain("space-y-2");
  });

  it("não exibe threads quando recolhido e não está pesquisando", () => {
    const ws = makeWorkspace("ws3");
    render(
      <WorkspaceGroup
        workspace={ws}
        threads={[makeThread("t1")]}
        isSearching={false}
        isCollapsed={true}
        currentThreadId=""
        onToggle={noop}
        onSelect={noop}
        onDelete={noop}
        onRename={noop}
        onTogglePin={noop}
      />,
    );
    expect(screen.queryByText("Conversa t1")).toBeNull();
  });

  it("exibe threads quando pesquisando mesmo recolhido", () => {
    const ws = makeWorkspace("ws4");
    render(
      <WorkspaceGroup
        workspace={ws}
        threads={[makeThread("t2")]}
        isSearching={true}
        isCollapsed={true}
        currentThreadId=""
        onToggle={noop}
        onSelect={noop}
        onDelete={noop}
        onRename={noop}
        onTogglePin={noop}
      />,
    );
    expect(screen.getByText("Conversa t2")).toBeInTheDocument();
  });
});

describe("SidebarFooter — ícones inline sem labels", () => {
  it("renderiza os controles de documentação e feedback com title", () => {
    render(<SidebarFooter />);
    const controls = document.querySelectorAll("[title]");
    expect(controls).toHaveLength(2);
    expect(controls[0].getAttribute("title")).toBe("Documentação");
    expect(controls[1].getAttribute("title")).toBe("Feedback");
  });

  it("não renderiza texto de label visível inline", () => {
    render(<SidebarFooter />);
    expect(screen.queryByText("Documentação")).toBeNull();
    expect(screen.queryByText("Feedback")).toBeNull();
  });

  it("links usam py-1.5 no container (não py-2)", () => {
    const { container } = render(<SidebarFooter />);
    const footer = container.firstElementChild as HTMLElement;
    expect(footer.className).toContain("pt-1.5");
  });

  it("mantém o foco dentro do diálogo de feedback", () => {
    render(<SidebarFooter />);
    fireEvent.click(screen.getByTitle("Feedback"));
    const dialog = screen.getByRole("dialog");
    const controls = Array.from(
      dialog.querySelectorAll<HTMLElement>("select, textarea, button"),
    );
    controls[controls.length - 1].focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(controls[0]);

    controls[0].focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(controls[controls.length - 1]);
  });

  it("associa nomes acessíveis aos campos de feedback", () => {
    render(<SidebarFooter />);
    fireEvent.click(screen.getByTitle("Feedback"));
    expect(screen.getByRole("combobox")).toHaveAttribute("id", "feedback-kind");
    expect(screen.getByLabelText("Descreva o problema")).toBeInTheDocument();
  });

  it("anuncia sucesso do feedback para leitores de tela", async () => {
    render(<SidebarFooter />);
    fireEvent.click(screen.getByTitle("Feedback"));
    fireEvent.change(screen.getByLabelText("Descreva o problema"), {
      target: { value: "um feedback" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Enviado");
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  });

  it("não inclui contexto técnico por padrão", async () => {
    render(<SidebarFooter />);
    fireEvent.click(screen.getByTitle("Feedback"));
    fireEvent.change(screen.getByLabelText("Descreva o problema"), {
      target: { value: "sem contexto" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));
    await screen.findByRole("status");
    expect(submitFeedback).toHaveBeenCalledWith({
      kind: "bug",
      description: "sem contexto",
      include_context: false,
    });
  });

  it("inclui contexto técnico quando o usuário habilita a opção", async () => {
    render(<SidebarFooter />);
    fireEvent.click(screen.getByTitle("Feedback"));
    fireEvent.change(screen.getByLabelText("Descreva o problema"), {
      target: { value: "com contexto" },
    });
    fireEvent.click(screen.getByLabelText("Incluir contexto técnico"));
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));
    await screen.findByRole("status");
    expect(submitFeedback).toHaveBeenCalledWith(
      expect.objectContaining({
        include_context: true,
        context: expect.objectContaining({ route: expect.any(String) }),
      }),
    );
  });

  it("anuncia descrição vazia como alerta acessível", () => {
    render(<SidebarFooter />);
    fireEvent.click(screen.getByTitle("Feedback"));
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Descreva o problema");
    expect(screen.getByRole("alert")).toHaveAttribute("aria-live", "assertive");
  });

  it("ignora submits concorrentes antes da primeira resposta", async () => {
    let resolveSubmit!: (value: { id: string }) => void;
    const pending = new Promise<{ id: string }>((resolve) => {
      resolveSubmit = resolve;
    });
    vi.mocked(submitFeedback).mockReturnValueOnce(pending);

    render(<SidebarFooter />);
    fireEvent.click(screen.getByTitle("Feedback"));
    fireEvent.change(screen.getByLabelText("Descreva o problema"), {
      target: { value: "um feedback" },
    });
    const dialog = screen.getByRole("dialog");
    act(() => {
      fireEvent.submit(dialog);
      fireEvent.submit(dialog);
    });

    expect(submitFeedback).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveSubmit({ id: "feedback-1" });
      await pending;
    });
  });
});
