// @vitest-environment jsdom
/**
 * PlanTab — Accordion multi-item unificando artifacts + write_todos: lista
 * única, itens colapsáveis, vários abertos ao mesmo tempo, markdown real
 * por artifact, ícone/cor por `artifact_type`.
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useWorkbenchStore } from "@/lib/stores/workbench-store";
import { m } from "@/lib/paraglide/messages";
import { PlanTab } from "../tabs/plan-tab";

vi.mock("@/lib/api/vectora-client", () => ({
  getThreadActivity: vi.fn().mockResolvedValue({ files_touched: [] }),
}));

const fetchMock = vi.fn();

function renderPlanTab(threadId: string) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <PlanTab threadId={threadId} />
    </QueryClientProvider>,
  );
}

function openAccordionItem(name: RegExp) {
  fireEvent.click(screen.getByRole("button", { name }));
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation((url: string) => {
    if (typeof url === "string" && url.includes("/artifacts/")) {
      return Promise.resolve({
        ok: true,
        json: async () => ({ content: "# Conteúdo\n\n**negrito** aqui" }),
      });
    }
    return Promise.resolve({ ok: true, json: async () => ({ artifacts: [] }) });
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PlanTab — Tasks (write_todos) dentro do Accordion", () => {
  it("renderiza os 3 estados de status (pending/in_progress/completed) ao abrir o item", async () => {
    const threadId = "t-tasks-1";
    useWorkbenchStore.getState().setPlanItems(threadId, [
      {
        title: "doc.md",
        path: "/doc.md",
        session_id: threadId,
        created_at: "2025",
      },
    ]);
    useWorkbenchStore.getState().setTodos(threadId, [
      { content: "passo pendente", status: "pending" },
      { content: "passo em andamento", status: "in_progress" },
      { content: "passo concluído", status: "completed" },
    ]);

    renderPlanTab(threadId);
    await screen.findByText("doc.md");
    openAccordionItem(/Tasks/);

    expect(await screen.findByText("passo pendente")).toBeInTheDocument();
    expect(screen.getByText("passo em andamento")).toBeInTheDocument();
    const completed = screen.getByText("passo concluído");
    expect(completed).toBeInTheDocument();
    expect(completed.className).toContain("line-through");
  });

  it("não renderiza a seção Tasks quando não há todos", async () => {
    const threadId = "t-tasks-2";
    useWorkbenchStore.getState().setPlanItems(threadId, [
      {
        title: "doc.md",
        path: "/doc.md",
        session_id: threadId,
        created_at: "2025",
      },
    ]);
    useWorkbenchStore.getState().setTodos(threadId, []);

    renderPlanTab(threadId);

    await screen.findByText("doc.md");
    expect(screen.queryByText(/Tasks/)).not.toBeInTheDocument();
  });

  it("mostra os todos mesmo sem nenhum artifact (Plan Mode via write_todos)", async () => {
    const threadId = "t-tasks-no-artifact";
    useWorkbenchStore.getState().setPlanItems(threadId, []);
    useWorkbenchStore.getState().setTodos(threadId, [
      { content: "desenhar o tabuleiro", status: "completed" },
      { content: "loop de movimento", status: "in_progress" },
      { content: "detecção de colisão", status: "pending" },
    ]);

    renderPlanTab(threadId);
    openAccordionItem(/Tasks/);

    expect(await screen.findByText("loop de movimento")).toBeInTheDocument();
    expect(screen.getByText("desenhar o tabuleiro")).toBeInTheDocument();
    expect(screen.getByText("detecção de colisão")).toBeInTheDocument();
    expect(
      screen.queryByText(m.workbench_plan_empty()),
    ).not.toBeInTheDocument();
  });

  it("empty-state só quando não há artifact NEM todos", async () => {
    const threadId = "t-tasks-truly-empty";
    useWorkbenchStore.getState().setPlanItems(threadId, []);
    useWorkbenchStore.getState().setTodos(threadId, []);

    renderPlanTab(threadId);

    expect(
      await screen.findByText(m.workbench_plan_empty()),
    ).toBeInTheDocument();
  });

  it("a lista de artifacts continua intacta — fonte de dados separada dos todos", async () => {
    const threadId = "t-tasks-3";
    useWorkbenchStore.getState().setPlanItems(threadId, [
      {
        title: "spec.md",
        path: "/spec.md",
        session_id: threadId,
        created_at: "2025",
      },
    ]);
    useWorkbenchStore
      .getState()
      .setTodos(threadId, [{ content: "passo 1", status: "in_progress" }]);

    renderPlanTab(threadId);
    await screen.findByText("spec.md");
    openAccordionItem(/Tasks/);

    expect(await screen.findByText("passo 1")).toBeInTheDocument();
  });
});

describe("PlanTab — Accordion multi-item", () => {
  it("publica atualização terminal quando o conteúdo do artifact falha", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/artifacts/") && !url.includes("/artifacts/?")) {
        return Promise.reject(new Error("artifact indisponível"));
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ artifacts: [] }),
      });
    });
    const threadId = "t-artifact-error";
    const item = {
      title: "Artifact com falha",
      path: "/falha.md",
      session_id: threadId,
      created_at: "2025-01-01",
    };
    useWorkbenchStore.getState().setPlanItems(threadId, [item]);
    const onOpenPlanDocument = vi.fn();

    render(
      <QueryClientProvider client={new QueryClient()}>
        <PlanTab threadId={threadId} onOpenPlanDocument={onOpenPlanDocument} />
      </QueryClientProvider>,
    );
    await screen.findByRole("button", { name: /Artifact com falha/ });

    openAccordionItem(/Artifact com falha/);

    await waitFor(() => {
      expect(onOpenPlanDocument).toHaveBeenLastCalledWith(
        item,
        null,
        m.workbench_git_operation_failed(),
        "update",
      );
    });
    expect(
      onOpenPlanDocument.mock.calls.some(([, , , phase]) => phase === "update"),
    ).toBe(true);
  });

  it("todos os títulos viram AccordionTrigger (botões clicáveis)", async () => {
    const threadId = "t-accordion-1";
    useWorkbenchStore.getState().setPlanItems(threadId, [
      {
        title: "Plano de Implementação",
        path: "/plano.md",
        session_id: threadId,
        created_at: "2025-01-02",
        artifact_type: "plan",
      },
      {
        title: "Especificação Técnica",
        path: "/spec.md",
        session_id: threadId,
        created_at: "2025-01-01",
        artifact_type: "spec",
      },
    ]);

    renderPlanTab(threadId);

    expect(
      await screen.findByRole("button", { name: /Plano de Implementação/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Especificação Técnica/ }),
    ).toBeInTheDocument();
  });

  it("título longo quebra em várias linhas em vez de truncar com '...'", async () => {
    // Sem min-w-0 no AccordionTrigger, o botão estoura a largura do painel
    // em vez de conter o título.
    const threadId = "t-title-wrap";
    const longTitle =
      "plano de implementação do jogo da cobrinha em godot 4.7 com todos os detalhes";
    useWorkbenchStore.getState().setPlanItems(threadId, [
      {
        title: longTitle,
        path: "/plano-longo.md",
        session_id: threadId,
        created_at: "2025-01-02",
        artifact_type: "plan",
      },
    ]);

    renderPlanTab(threadId);

    const titleEl = await screen.findByText(longTitle);
    expect(titleEl.className).toContain("whitespace-normal");
    expect(titleEl.className).toContain("break-words");
    expect(titleEl.className).not.toContain("truncate");
  });

  it("clicar dois triggers deixa ambos abertos simultaneamente (type=multiple)", async () => {
    const threadId = "t-accordion-2";
    useWorkbenchStore.getState().setPlanItems(threadId, [
      {
        title: "Plano A",
        path: "/plano-a.md",
        session_id: threadId,
        created_at: "2025-01-02",
        artifact_type: "plan",
      },
      {
        title: "Guia B",
        path: "/guia-b.md",
        session_id: threadId,
        created_at: "2025-01-01",
        artifact_type: "guide",
      },
    ]);

    renderPlanTab(threadId);
    await screen.findByRole("button", { name: /Plano A/ });

    openAccordionItem(/Plano A/);
    openAccordionItem(/Guia B/);

    // Markdown de ambos aparece — nenhum fechou o outro ao abrir.
    const bold = await screen.findAllByText("negrito");
    expect(bold.length).toBe(2);
  });

  it("renderiza markdown real (**negrito** vira <strong>), não texto cru", async () => {
    const threadId = "t-accordion-md";
    useWorkbenchStore.getState().setPlanItems(threadId, [
      {
        title: "Documento com Markdown",
        path: "/doc-md.md",
        session_id: threadId,
        created_at: "2025-01-01",
        artifact_type: "guide",
      },
    ]);

    renderPlanTab(threadId);
    await screen.findByRole("button", { name: /Documento com Markdown/ });
    openAccordionItem(/Documento com Markdown/);

    const bold = await screen.findByText("negrito");
    expect(bold.tagName.toLowerCase()).toBe("strong");
  });

  it("artifact sem artifact_type cai no ícone/cor fallback sem quebrar", async () => {
    const threadId = "t-accordion-notype";
    useWorkbenchStore.getState().setPlanItems(threadId, [
      {
        title: "Artifact Legado Sem Tipo",
        path: "/legado.md",
        session_id: threadId,
        created_at: "2025-01-01",
        // artifact_type ausente de propósito (artifact criado antes do campo existir).
      },
    ]);

    expect(() => renderPlanTab(threadId)).not.toThrow();
    expect(
      await screen.findByRole("button", {
        name: /Artifact Legado Sem Tipo/,
      }),
    ).toBeInTheDocument();
  });

  it("FilesTouchedSection continua fora do Accordion (rodapé próprio, sempre visível)", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/artifacts/")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ content: "" }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ artifacts: [] }),
      });
    });
    const { getThreadActivity } = await import("@/lib/api/vectora-client");
    vi.mocked(getThreadActivity).mockResolvedValueOnce({
      files_touched: ["src/a.ts"],
      tool_call_counts: {},
      turn_count: 1,
    });

    const threadId = "t-files-touched";
    useWorkbenchStore.getState().setPlanItems(threadId, [
      {
        title: "Plano X",
        path: "/plano-x.md",
        session_id: threadId,
        created_at: "2025-01-01",
      },
    ]);

    const { container } = renderPlanTab(threadId);
    await screen.findByRole("button", { name: /Plano X/ });

    const footer = await screen.findByText(/Files touched/);
    // Fora do <Accordion> — não é um AccordionTrigger.
    expect(
      within(container).queryAllByRole("button", { name: /Plano X/ }),
    ).toHaveLength(1);
    expect(footer.closest('[data-slot="accordion"]')).toBeNull();
  });
});
