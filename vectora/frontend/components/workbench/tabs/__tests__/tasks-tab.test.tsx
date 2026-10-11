// @vitest-environment jsdom
/**
 * TasksTab — delegação de subagente (trigger_type/kind "subagent") aparece
 * com o badge próprio e sem os controles de rodar-agora/toggle, que não se
 * aplicam a uma tarefa-âncora auto-gerenciada pelo backend.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { TasksTab } from "../tasks-tab";

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get:
        (_t, prop) =>
        (...args: unknown[]) =>
          args.length
            ? `${String(prop)}(${JSON.stringify(args[0])})`
            : String(prop),
    },
  ),
}));

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (sel: (s: { getActive: () => undefined }) => unknown) =>
    sel({ getActive: () => undefined }),
}));

vi.mock("@/lib/hooks/use-webhook-events", () => ({
  useWebhookEvents: () => {},
}));

const mockTasks = vi.fn();
vi.mock("@/lib/hooks/use-background-tasks", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/hooks/use-background-tasks")
  >("@/lib/hooks/use-background-tasks");
  return {
    ...actual,
    useBackgroundTasks: () => mockTasks(),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function baseHook(overrides: Partial<ReturnType<typeof mockTasks>> = {}) {
  return {
    tasks: [],
    runs: [],
    loading: false,
    refetch: vi.fn(),
    createTask: vi.fn(),
    toggleTask: vi.fn(),
    deleteTask: vi.fn(),
    runTask: vi.fn(),
    ...overrides,
  };
}

function runsHook(summary: string, status = "done") {
  return baseHook({
    runs: [
      {
        id: "run-1",
        task_id: "t1",
        run_thread_id: "bg-t1-1",
        trigger_source: "manual",
        status,
        summary,
        started_at: "2025",
        finished_at: "2025",
      },
    ],
  });
}

describe("TasksTab", () => {
  it("mostra o badge de subagente e esconde rodar-agora/toggle pra tarefa-âncora", () => {
    mockTasks.mockReturnValue(
      baseHook({
        tasks: [
          {
            id: "t1",
            session_id: "s1",
            workspace_id: null,
            kind: "subagent",
            name: "Subagente: coder",
            instruction: "",
            trigger_type: "subagent",
            trigger_config: { subagent_type: "coder" },
            enabled: true,
            last_run_at: null,
            next_run_at: null,
          },
        ],
      }),
    );

    render(<TasksTab threadId="t1" />);

    expect(
      screen.getAllByText("background_trigger_subagent").length,
    ).toBeGreaterThan(0);
    expect(screen.queryByTestId("background-run-now")).not.toBeInTheDocument();
  });

  it("run awaiting_approval mostra aprovar/rejeitar/cancelar e aprova via endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    const refetch = vi.fn();

    mockTasks.mockReturnValue(
      baseHook({
        refetch,
        runs: [
          {
            id: "run-1",
            task_id: "t1",
            run_thread_id: "bg-t1-1",
            trigger_source: "manual",
            status: "awaiting_approval",
            summary: "aguardando terminal",
            started_at: "2025",
            finished_at: null,
          },
        ],
      }),
    );

    render(<TasksTab threadId="thr-1" />);

    const approve = screen.getByText("background_approve");
    expect(approve).toBeInTheDocument();
    expect(screen.getByText("background_reject")).toBeInTheDocument();
    expect(screen.getByText("background_cancel_run")).toBeInTheDocument();

    fireEvent.click(approve);
    expect(fetchMock).toHaveBeenCalledWith(
      "/sessions/thr-1/background/runs/run-1/resume",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ decision: "approve" }),
      }),
    );
    await vi.waitFor(() => expect(refetch).toHaveBeenCalledTimes(1));
    vi.unstubAllGlobals();
  });

  it("rejeitar envia decision='reject' e refetch depois; cancelar envia decision='cancel'", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    const refetch = vi.fn();

    mockTasks.mockReturnValue(
      baseHook({
        refetch,
        runs: [
          {
            id: "run-2",
            task_id: "t1",
            run_thread_id: "bg-t1-2",
            trigger_source: "manual",
            status: "awaiting_approval",
            summary: "aguardando terminal",
            started_at: "2025",
            finished_at: null,
          },
        ],
      }),
    );

    render(<TasksTab threadId="thr-2" />);

    fireEvent.click(screen.getByText("background_reject"));
    expect(fetchMock).toHaveBeenCalledWith(
      "/sessions/thr-2/background/runs/run-2/resume",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ decision: "reject" }),
      }),
    );
    await vi.waitFor(() => expect(refetch).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByText("background_cancel_run"));
    expect(fetchMock).toHaveBeenCalledWith(
      "/sessions/thr-2/background/runs/run-2/resume",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ decision: "cancel" }),
      }),
    );
    await vi.waitFor(() => expect(refetch).toHaveBeenCalledTimes(2));
    vi.unstubAllGlobals();
  });

  it("lista scrollável tem min-h-0 (evita overflow quando o painel é redimensionado pequeno)", () => {
    mockTasks.mockReturnValue(
      baseHook({
        tasks: [
          {
            id: "t1",
            session_id: "s1",
            workspace_id: null,
            kind: "cron",
            name: "Tarefa normal",
            instruction: "",
            trigger_type: "cron",
            trigger_config: {},
            enabled: true,
            last_run_at: null,
            next_run_at: null,
          },
        ],
      }),
    );

    const { container } = render(<TasksTab threadId="t1" />);

    const scrollable = container.querySelector(".overflow-y-auto");
    expect(scrollable).not.toBeNull();
    expect(scrollable?.className).toContain("min-h-0");
  });

  it("mantém rodar-agora e toggle pra tarefas normais (regressão)", () => {
    mockTasks.mockReturnValue(
      baseHook({
        tasks: [
          {
            id: "t2",
            session_id: "s1",
            workspace_id: null,
            kind: "routine",
            name: "Resumo diário",
            instruction: "Resuma o dia",
            trigger_type: "interval",
            trigger_config: { cron_expr: "0 9 * * *" },
            enabled: true,
            last_run_at: null,
            next_run_at: null,
          },
        ],
      }),
    );

    render(<TasksTab threadId="t1" />);

    expect(screen.getByTestId("background-run-now")).toBeInTheDocument();
  });

  describe("card de execução colapsado", () => {
    it("mostra o card fechado por padrão (aria-expanded=false)", () => {
      mockTasks.mockReturnValue(
        runsHook(
          "Command(update={'files': {'a.py': 'conteúdo bem longo...'}})",
        ),
      );

      render(<TasksTab threadId="t1" />);

      expect(
        screen.getByRole("button", { name: /background_status_done/ }),
      ).toHaveAttribute("aria-expanded", "false");
    });

    it("clicar no card expande e mostra o summary completo em <pre>", () => {
      const longSummary =
        "Command(update={'files': {'a.py': 'conteúdo bem longo que não cabe numa linha só'}})";
      mockTasks.mockReturnValue(runsHook(longSummary));

      render(<TasksTab threadId="t1" />);
      const toggle = screen.getByRole("button", {
        name: /background_status_done/,
      });
      fireEvent.click(toggle);

      expect(toggle).toHaveAttribute("aria-expanded", "true");
      const pre = document.querySelector("pre");
      expect(pre?.textContent).toBe(longSummary);
    });

    it("erro/borda: run sem summary não vira botão clicável (nada pra expandir)", () => {
      mockTasks.mockReturnValue(runsHook(""));

      render(<TasksTab threadId="t1" />);

      const toggle = screen.getByRole("button", {
        name: /background_status_done/,
      });
      expect(toggle).toBeDisabled();
    });

    it("múltiplos cards expandem independentemente", () => {
      mockTasks.mockReturnValue(
        baseHook({
          runs: [
            {
              id: "run-1",
              task_id: "t1",
              run_thread_id: "bg-t1-1",
              trigger_source: "manual",
              status: "done",
              summary: "resumo 1",
              started_at: "2025",
              finished_at: "2025",
            },
            {
              id: "run-2",
              task_id: "t1",
              run_thread_id: "bg-t1-2",
              trigger_source: "manual",
              status: "error",
              summary: "resumo 2",
              started_at: "2025",
              finished_at: "2025",
            },
          ],
        }),
      );

      render(<TasksTab threadId="t1" />);
      const toggles = screen.getAllByRole("button", {
        name: /background_status/,
      });
      fireEvent.click(toggles[0]);

      expect(toggles[0]).toHaveAttribute("aria-expanded", "true");
      expect(toggles[1]).toHaveAttribute("aria-expanded", "false");
    });
  });
});
