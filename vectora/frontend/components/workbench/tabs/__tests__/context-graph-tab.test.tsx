// @vitest-environment jsdom
/**
 * ContextGraphTab — renderização condicional por status + interações.
 *
 * Testa: not_built / running / error / done, botão Construir, perguntas
 * sugeridas clicáveis, god nodes clicáveis e crédito do graphify.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  act,
  cleanup,
} from "@testing-library/react";

// ── mocks de dependências ────────────────────────────────────────────────────

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get: (_t, prop) => (args?: { n?: number }) => {
        if (prop === "graph_nodes_count") return `${args?.n ?? 0} nós`;
        if (prop === "graph_edges_count") return `${args?.n ?? 0} arestas`;
        return String(prop);
      },
    },
  ),
}));

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (sel: (s: { active_id: string }) => unknown) =>
    sel({ active_id: "ws1" }),
}));

const mockBuild = vi.fn();
const mockUpdate = vi.fn();
const mockResume = vi.fn();
const mockCancel = vi.fn(() => Promise.resolve());
const mockQueryAffected = vi.fn(() => Promise.resolve(""));
const mockGetHtmlUrl = vi.fn(() => "/workspaces/ws1/context-graph/html");
const mockFetchStatus = vi.fn();
const mockFetchGraphData = vi.fn(() =>
  Promise.resolve({ nodes: [], links: [] }),
);
const mockPathBetween = vi.fn(() =>
  Promise.resolve({ answer: "", nodes: [], edges: [] }),
);
const mockUseContextGraph = vi.fn();

vi.mock("@/lib/hooks/use-context-graph", () => ({
  useContextGraph: (...args: unknown[]) => mockUseContextGraph(...args),
}));

vi.mock("@/components/workbench/tabs/context-graph-viewer", () => ({
  ContextGraphViewer: () => <div data-testid="graph-canvas-mock" />,
}));

// reagraph renderiza um canvas WebGL real (three.js) — sem suporte em jsdom.
// Stub minimalista só pra provar que o componente nativo é montado (em vez
// do antigo iframe), sem depender de um contexto GL que jsdom não tem.
vi.mock("reagraph", () => ({
  GraphCanvas: () => <div data-testid="graph-canvas-mock" />,
  lightTheme: {},
  darkTheme: {},
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "light" }),
}));

// ── helpers ──────────────────────────────────────────────────────────────────

function setup(
  overrides: {
    status?: object;
    report?: string | null;
    loading?: boolean;
  } = {},
) {
  mockUseContextGraph.mockReturnValue({
    status: { status: "not_built" },
    report: null,
    loading: false,
    build: mockBuild,
    update: mockUpdate,
    resume: mockResume,
    cancel: mockCancel,
    queryAffected: mockQueryAffected,
    getHtmlUrl: mockGetHtmlUrl,
    fetchStatus: mockFetchStatus,
    fetchGraphData: mockFetchGraphData,
    pathBetween: mockPathBetween,
    ...overrides,
  });
}

afterEach(async () => {
  // ContextGraphViewer busca o grafo (fetchGraphData) num efeito assíncrono
  // ao montar — sem drenar essa microtask dentro de act() antes do cleanup,
  // o setState do resultado dispara depois que o teste já terminou e o
  // React avisa "not wrapped in act(...)" em todo teste que renderiza o
  // status "done", mesmo os que não têm nada a ver com o grafo em si.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  cleanup();
  vi.clearAllMocks();
});

// Importação após os mocks
import {
  ContextGraphTab,
  graphStageColor,
} from "@/components/workbench/tabs/context-graph-tab";

// ── testes ───────────────────────────────────────────────────────────────────

describe("ContextGraphTab", () => {
  describe("estado not_built", () => {
    it("exibe mensagem graph_not_built", () => {
      setup();
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByText("graph_not_built")).toBeTruthy();
    });

    it("botão mostra graph_build_button só uma vez (sem CTA duplicado)", () => {
      setup();
      render(<ContextGraphTab threadId="t1" />);
      // Só o botão da barra de ações — não há CTA proeminente duplicado
      // no estado vazio.
      expect(screen.getAllByText("graph_build_button")).toHaveLength(1);
      expect(screen.getAllByTestId("graph-build-btn")).toHaveLength(1);
    });

    it("clicar no botão chama build() com mode + fileTypes dos settings", async () => {
      setup();
      render(<ContextGraphTab threadId="t1" />);
      const btn = document.querySelector("button") as HTMLButtonElement;
      await act(async () => {
        fireEvent.click(btn);
      });
      expect(mockBuild).toHaveBeenCalledTimes(1);
      expect(mockBuild).toHaveBeenCalledWith(
        expect.objectContaining({
          mode: "semantic",
          fileTypes: expect.arrayContaining(["code", "document", "paper"]),
        }),
      );
    });

    it("botão está habilitado em not_built", () => {
      setup();
      render(<ContextGraphTab threadId="t1" />);
      const btn = document.querySelector("button") as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
    });

    it("o gear abre o painel de settings (tipos + modo)", async () => {
      setup();
      render(<ContextGraphTab threadId="t1" />);
      expect(
        screen.queryByTestId("graph-settings-panel"),
      ).not.toBeInTheDocument();
      await act(async () => {
        fireEvent.click(screen.getByTestId("graph-settings-btn"));
      });
      expect(screen.getByTestId("graph-settings-panel")).toBeInTheDocument();
      expect(screen.getByText("graph_filetype_document")).toBeTruthy();
      expect(screen.getByText("graph_mode_semantic")).toBeTruthy();
    });
  });

  describe("estado running", () => {
    it("exibe graph_building", () => {
      setup({ status: { status: "running" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getAllByText("graph_building").length).toBeGreaterThan(0);
    });

    it("botão vira cancelar e fica habilitado durante running", () => {
      setup({ status: { status: "running" } });
      render(<ContextGraphTab threadId="t1" />);
      const btn = document.querySelector(
        "[data-testid='graph-build-btn']",
      ) as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
      expect(btn.textContent).toContain("graph_cancel_button");
    });

    it("botão está desabilitado quando loading=true", () => {
      setup({ loading: true });
      render(<ContextGraphTab threadId="t1" />);
      const btn = document.querySelector(
        "[data-testid='graph-build-btn']",
      ) as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
    });

    it("exibe botão cancelar durante running", () => {
      setup({ status: { status: "running" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByText("graph_cancel_button")).toBeTruthy();
    });

    it("clicar no cancelar chama cancel()", async () => {
      setup({ status: { status: "running" } });
      render(<ContextGraphTab threadId="t1" />);
      await act(async () => {
        fireEvent.click(screen.getByText("graph_cancel_button"));
      });
      expect(mockCancel).toHaveBeenCalledTimes(1);
    });

    it("not_built não exibe botão cancelar", () => {
      setup();
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.queryByText("graph_cancel_button")).toBeNull();
    });

    it("exibe barra de progresso quando step está presente", () => {
      setup({
        status: {
          status: "running",
          step: 3,
          step_total: 9,
          step_label: "Extraindo AST...",
          files_total: 12,
          files_done: 4,
          files_list: ["src/a.ts", "src/b.ts"],
        },
      });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByText("Extraindo AST...")).toBeTruthy();
      expect(screen.getByText("3/9")).toBeTruthy();
      expect(screen.getByText("graph_files_progress")).toBeTruthy();
      expect(screen.getByText("src/a.ts")).toBeTruthy();
    });
  });

  describe("estado error", () => {
    it("exibe a mensagem de erro", () => {
      setup({ status: { status: "error", error: "Pipeline falhou" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByText("Pipeline falhou")).toBeTruthy();
    });

    it("exibe mensagem genérica se error é null", () => {
      setup({ status: { status: "error", error: null } });
      render(<ContextGraphTab threadId="t1" />);
      expect(
        screen.getByText(/Erro desconhecido|workbench_settings_unknown_error/),
      ).toBeTruthy();
    });
  });

  describe("estado done", () => {
    it("mantém o grafo disponível e informa degradação do índice semântico", () => {
      setup({
        status: {
          status: "degraded",
          node_count: 42,
          edge_count: 17,
          error: "Índice semântico indisponível",
        },
      });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByTestId("graph-degraded")).toBeTruthy();
      expect(screen.getByText("Índice semântico indisponível")).toBeTruthy();
    });

    it("exibe contagem de nós e arestas", () => {
      setup({ status: { status: "done", node_count: 42, edge_count: 17 } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByText("42 nós")).toBeTruthy();
      expect(screen.getByText("17 arestas")).toBeTruthy();
    });

    it("botão muda para graph_rebuild_button", () => {
      setup({ status: { status: "done" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByText("graph_rebuild_button")).toBeTruthy();
    });

    it("renderiza o grafo nativo em vez de iframe/link externo", async () => {
      setup({ status: { status: "done", node_count: 42, edge_count: 17 } });
      render(<ContextGraphTab threadId="t1" />);
      await screen.findByTestId("graph-canvas-mock");
      // Nenhum iframe nem link externo aponta pro HTML do grafo — só o
      // canvas nativo (o link de crédito do rodapé, esse sim externo,
      // continua existindo).
      expect(document.querySelector("iframe")).toBeNull();
      expect(
        document.querySelector("a[href*='context-graph/html']"),
      ).toBeNull();
    });
  });

  describe("estado done — grafo vazio (build ok, 0 arquivos compatíveis)", () => {
    it("sem node_count, mostra o estado de resultado vazio em vez do grafo", () => {
      setup({ status: { status: "done" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByTestId("graph-empty-result")).toBeTruthy();
      expect(screen.getByText("graph_empty_result")).toBeTruthy();
      expect(screen.queryByTestId("graph-canvas-mock")).toBeNull();
    });

    it("node_count 0, mostra o mesmo estado de resultado vazio", () => {
      setup({ status: { status: "done", node_count: 0, edge_count: 0 } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByTestId("graph-empty-result")).toBeTruthy();
      expect(screen.queryByTestId("graph-canvas-mock")).toBeNull();
    });
  });

  describe("perguntas sugeridas e god nodes", () => {
    const REPORT =
      "**God nodes** — top conectados\n- AuthService\n- TokenUtils\n\n**Perguntas sugeridas**\n- O que faz login?\n- Como Token é gerado?\n";

    it("perguntas clicáveis chamam onSendPrompt com texto da pergunta", async () => {
      const onSendPrompt = vi.fn();
      setup({ status: { status: "done", node_count: 1 }, report: REPORT });
      render(<ContextGraphTab threadId="t1" onSendPrompt={onSendPrompt} />);
      const btn = screen.getByText("O que faz login?");
      await act(async () => {
        fireEvent.click(btn);
      });
      expect(onSendPrompt).toHaveBeenCalledWith("O que faz login?");
    });

    it("segunda pergunta também envia via onSendPrompt", async () => {
      const onSendPrompt = vi.fn();
      setup({ status: { status: "done", node_count: 1 }, report: REPORT });
      render(<ContextGraphTab threadId="t1" onSendPrompt={onSendPrompt} />);
      const btn = screen.getByText("Como Token é gerado?");
      await act(async () => {
        fireEvent.click(btn);
      });
      expect(onSendPrompt).toHaveBeenCalledWith("Como Token é gerado?");
    });

    it("god nodes clicáveis enviam prompt de explain com o nome do nó", async () => {
      const onSendPrompt = vi.fn();
      setup({ status: { status: "done", node_count: 1 }, report: REPORT });
      render(<ContextGraphTab threadId="t1" onSendPrompt={onSendPrompt} />);
      const btn = screen.getByText("AuthService");
      await act(async () => {
        fireEvent.click(btn);
      });
      expect(onSendPrompt).toHaveBeenCalledWith(
        expect.stringContaining("AuthService"),
      );
    });

    it("sem onSendPrompt clicar na pergunta não lança erro", async () => {
      setup({ status: { status: "done", node_count: 1 }, report: REPORT });
      render(<ContextGraphTab threadId="t1" />);
      const btn = screen.getByText("O que faz login?");
      await expect(
        act(async () => {
          fireEvent.click(btn);
        }),
      ).resolves.not.toThrow();
    });
  });

  describe("crédito e report toggle", () => {
    it("exibe crédito do graphify no rodapé como link clicável pro repositório", () => {
      setup();
      render(<ContextGraphTab threadId="t1" />);
      const credit = screen.getByText("graph_credit");
      expect(credit.tagName).toBe("A");
      expect(credit.getAttribute("href")).toBe(
        "https://github.com/safishamsi/graphify",
      );
      expect(credit.getAttribute("target")).toBe("_blank");
      // rel="noopener" é obrigatório com target="_blank" — evita a página
      // aberta reusar o `window.opener` (regressão de segurança comum).
      expect(credit.getAttribute("rel")).toContain("noopener");
    });

    it("toggle do report expande e colapsa o markdown", async () => {
      const REPORT_MD =
        "**God nodes**\n- X\n\n**Perguntas sugeridas**\n- Algo?\n";
      setup({ status: { status: "done", node_count: 1 }, report: REPORT_MD });
      render(<ContextGraphTab threadId="t1" />);

      const toggleBtn = screen.getByText("graph_report_title");
      await act(async () => {
        fireEvent.click(toggleBtn);
      });
      const pre = document.querySelector("pre");
      expect(pre).not.toBeNull();
      expect(pre!.textContent).toContain("God nodes");

      await act(async () => {
        fireEvent.click(screen.getByText("Ocultar"));
      });
      expect(document.querySelector("pre")).toBeNull();
    });
  });

  describe("Atualizar e afetados", () => {
    it("estado queued exibe graph_building", () => {
      setup({ status: { status: "queued" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getAllByText("graph_building").length).toBeGreaterThan(0);
    });

    it("done exibe botão graph_update_button", () => {
      setup({ status: { status: "done" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByText("graph_update_button")).toBeTruthy();
    });

    it("not_built não exibe botão Atualizar", () => {
      setup();
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.queryByText("graph_update_button")).toBeNull();
    });

    it("running não exibe botão Atualizar", () => {
      setup({ status: { status: "running" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.queryByText("graph_update_button")).toBeNull();
    });

    it("clicar em Atualizar chama update()", async () => {
      setup({ status: { status: "done" } });
      render(<ContextGraphTab threadId="t1" />);
      await act(async () => {
        fireEvent.click(screen.getByText("graph_update_button"));
      });
      expect(mockUpdate).toHaveBeenCalledTimes(1);
    });

    it("botão ↯ do god node chama queryAffected com o nó", async () => {
      setup({
        status: { status: "done", node_count: 1 },
        report: "**God nodes**\n- AuthService\n",
      });
      render(<ContextGraphTab threadId="t1" />);
      await act(async () => {
        fireEvent.click(screen.getByText("↯"));
      });
      expect(mockQueryAffected).toHaveBeenCalledWith("AuthService");
    });

    it("queryAffected com texto envia para o chat", async () => {
      mockQueryAffected.mockResolvedValueOnce("impacto: A, B");
      const onSendPrompt = vi.fn();
      setup({
        status: { status: "done", node_count: 1 },
        report: "**God nodes**\n- AuthService\n",
      });
      render(<ContextGraphTab threadId="t1" onSendPrompt={onSendPrompt} />);
      await act(async () => {
        fireEvent.click(screen.getByText("↯"));
      });
      expect(onSendPrompt).toHaveBeenCalledWith("impacto: A, B");
    });
  });

  describe("done — bordas", () => {
    it("node_count null não exibe contagem de nós", () => {
      setup({ status: { status: "done", node_count: null, edge_count: null } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.queryByText(/nós/)).toBeNull();
    });

    it("done sem report não renderiza god nodes", () => {
      setup({ status: { status: "done", node_count: 1 }, report: null });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.queryByText("graph_god_nodes_title")).toBeNull();
    });

    it("god nodes limitados a 8", () => {
      const lines = Array.from({ length: 12 }, (_, i) => `- God${i}`).join(
        "\n",
      );
      setup({
        status: { status: "done", node_count: 1 },
        report: `**God nodes**\n${lines}\n`,
      });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getAllByText(/^God\d+$/).length).toBeLessThanOrEqual(8);
    });

    it("perguntas limitadas a 5", () => {
      const qs = Array.from({ length: 8 }, (_, i) => `- Pergunta ${i}?`).join(
        "\n",
      );
      setup({
        status: { status: "done", node_count: 1 },
        report: `**Perguntas sugeridas**\n${qs}\n`,
      });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getAllByText(/^Pergunta \d\?$/).length).toBeLessThanOrEqual(
        5,
      );
    });
  });

  describe("estado paused (quota esgotada)", () => {
    it("exibe a mensagem graph_paused", () => {
      setup({ status: { status: "paused" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByText("graph_paused")).toBeTruthy();
    });

    it("renderiza o container data-testid=graph-paused", () => {
      setup({ status: { status: "paused" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(
        document.querySelector("[data-testid='graph-paused']"),
      ).toBeTruthy();
    });

    it("mostra o botão Continuar", () => {
      setup({ status: { status: "paused" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByText("graph_continue_button")).toBeTruthy();
    });

    it("clicar em Continuar retoma do checkpoint (chama resume, não build do zero)", () => {
      setup({ status: { status: "paused" } });
      render(<ContextGraphTab threadId="t1" />);
      fireEvent.click(screen.getByText("graph_continue_button"));
      expect(mockResume).toHaveBeenCalled();
      expect(mockBuild).not.toHaveBeenCalled();
      expect(mockUpdate).not.toHaveBeenCalled();
    });

    it("exibe a mensagem de erro da quota quando presente", () => {
      setup({ status: { status: "paused", error: "todos esgotaram" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByText("todos esgotaram")).toBeTruthy();
    });

    it("não exibe graph_not_built em paused", () => {
      setup({ status: { status: "paused" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.queryByText("graph_not_built")).toBeNull();
    });

    it("exibe contador step/step_total quando paused com progresso parcial", () => {
      setup({ status: { status: "paused", step: 3, step_total: 9 } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.getByText("3/9")).toBeTruthy();
    });

    it("não exibe contador step quando paused sem step info", () => {
      setup({ status: { status: "paused" } });
      render(<ContextGraphTab threadId="t1" />);
      expect(screen.queryByText(/\d+\/\d+/)).toBeNull();
    });
  });

  describe("graphStageColor — cor por etapa (highlights do theme)", () => {
    it("etapa AST (≤2) usa o highlight de AST", () => {
      expect(graphStageColor(1)).toBe("var(--color-graph-stage-ast)");
      expect(graphStageColor(2)).toBe("var(--color-graph-stage-ast)");
    });

    it("etapa semântica (3) usa o highlight de semântica", () => {
      expect(graphStageColor(3)).toBe("var(--color-graph-stage-semantic)");
    });

    it("etapas pós-semântica (≥4) usam o highlight de concluído", () => {
      expect(graphStageColor(4)).toBe("var(--color-graph-stage-done)");
      expect(graphStageColor(9)).toBe("var(--color-graph-stage-done)");
    });

    it("step nulo/indefinido cai no estágio inicial (AST)", () => {
      expect(graphStageColor(null)).toBe("var(--color-graph-stage-ast)");
      expect(graphStageColor(undefined)).toBe("var(--color-graph-stage-ast)");
    });
  });
});
