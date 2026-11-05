// @vitest-environment jsdom
/**
 * Board do 3º modo de interface, feature pública.
 *
 * Uma lane por status do backend (`KANBAN_STATUSES` menos `archived`, que
 * entra por filtro). Lane vazia colapsa em trilho quando o board tem
 * trabalho em outra; board totalmente vazio mostra todas expandidas.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";

import { overwriteGetLocale, baseLocale } from "@/lib/paraglide/runtime";
import { applyDragTransition, arcState, KanbanBoard } from "../kanban-board";

function mockTasks(tasks: unknown[]) {
  // A resposta real de GET /sessions/{id}/background/tasks é `list[TaskOut]`
  // — um array puro, não um envelope `{tasks: [...]}`. `/boards` (buscado
  // sem condição nenhuma ao montar o board) precisa da própria
  // resposta vazia — senão o array de tasks vaza pro switcher de board
  // como se cada task fosse um board, e nomes de task acabam aparecendo
  // duas vezes na árvore (o card + a opção espúria no <select>).
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/boards")) return new Response("[]", { status: 200 });
      return new Response(JSON.stringify(tasks), { status: 200 });
    }),
  );
}

/** Stub de fetch pros testes de multi-board — cobre
 * /boards (GET/POST), /boards/{id}/board e o fallback de sessão. */
function mockMultiBoard(
  opts: {
    boards?: unknown[];
    boardView?: { columns: { status: string; tasks: unknown[] }[] };
    sessionTasks?: unknown[];
  },
  chamadas?: { url: string; method: string; body?: string }[],
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      chamadas?.push({
        url,
        method: init?.method ?? "GET",
        body: init?.body as string | undefined,
      });
      if (url.endsWith("/boards") && (init?.method ?? "GET") === "GET") {
        return new Response(JSON.stringify(opts.boards ?? []), {
          status: 200,
        });
      }
      if (url.endsWith("/boards") && init?.method === "POST") {
        return new Response(
          JSON.stringify({ id: "board-novo", slug: "novo", name: "Novo" }),
          { status: 201 },
        );
      }
      if (url.includes("/boards/") && url.endsWith("/board")) {
        return new Response(JSON.stringify(opts.boardView ?? { columns: [] }), {
          status: 200,
        });
      }
      return new Response(JSON.stringify(opts.sessionTasks ?? []), {
        status: 200,
      });
    }),
  );
}

function task(over: Record<string, unknown> = {}) {
  return {
    id: "t1",
    name: "tarefa",
    status: "todo",
    block_kind: null,
    block_reason: null,
    ...over,
  };
}

// jsdom não implementa EventSource — o mock abaixo substitui o global em
// toda a suíte, não só nos testes de SSE, senão o `new EventSource(...)`
// do mount quebraria os testes que só olham pro polling/REST.
class MockEventSource {
  static instances: MockEventSource[] = [];
  closed = false;
  readonly url: string;
  private messageListeners: ((ev: MessageEvent<string>) => void)[] = [];
  private errorListeners: (() => void)[] = [];

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(
    type: "message" | "error",
    listener: (ev?: unknown) => void,
  ) {
    if (type === "message") {
      this.messageListeners.push(
        listener as (ev: MessageEvent<string>) => void,
      );
    } else {
      this.errorListeners.push(listener as () => void);
    }
  }

  close() {
    this.closed = true;
  }

  emit(data: unknown) {
    const ev = { data: JSON.stringify(data) } as MessageEvent<string>;
    for (const listener of this.messageListeners) listener(ev);
  }

  fail() {
    for (const listener of this.errorListeners) listener();
  }
}

beforeEach(() => {
  overwriteGetLocale(() => "pt");
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  overwriteGetLocale(() => baseLocale);
  // O board ativo persiste em localStorage por thread; sem limpar, um
  // teste que troca de board vaza o valor pro próximo
  // teste que monta a MESMA thread ("s1", usada em quase todo teste
  // deste arquivo).
  localStorage.clear();
});

async function montar() {
  render(<KanbanBoard threadId="s1" />);
  await act(async () => {});
}

describe("KanbanBoard", () => {
  it("põe cada card na coluna do seu status", async () => {
    mockTasks([
      task({ id: "a", name: "na fila", status: "todo" }),
      task({ id: "b", name: "rodando", status: "running" }),
      task({ id: "c", name: "concluída", status: "done" }),
    ]);

    await montar();

    expect(
      within(screen.getByTestId("kanban-col-todo")).getByText("na fila"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("kanban-col-running")).getByText("rodando"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("kanban-col-done")).getByText("concluída"),
    ).toBeInTheDocument();
  });

  it("card bloqueado mostra o motivo", async () => {
    mockTasks([
      task({
        status: "blocked",
        block_kind: "needs_input",
        block_reason: "falta a chave da API",
      }),
    ]);

    await montar();

    expect(screen.getByText("falta a chave da API")).toBeInTheDocument();
  });

  it("dependência aparece como contador N/M, não como coluna", async () => {
    mockTasks([
      task({
        status: "todo",
        dependencies: [
          { id: "p1", name: "pai concluído", status: "done" },
          { id: "p2", name: "pai pendente", status: "todo" },
        ],
      }),
    ]);

    await montar();

    expect(screen.getByText("1/2 dependências concluídas")).toBeInTheDocument();
    expect(screen.getByText("pai concluído")).toBeInTheDocument();
    expect(screen.getByText("pai pendente")).toBeInTheDocument();
  });

  it("task sem dependências não mostra o contador (edge)", async () => {
    mockTasks([task({ status: "todo", dependencies: [] })]);

    await montar();

    expect(
      screen.queryByText(/dependências concluídas/i),
    ).not.toBeInTheDocument();
  });

  it("botão 'Histórico de execuções' busca /runs sob demanda e lista o resultado", async () => {
    const chamadas: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        chamadas.push(url);
        if (url.endsWith("/runs")) {
          return new Response(
            JSON.stringify([
              {
                id: "r1",
                status: "done",
                trigger_source: "manual",
                started_at: "2026-01-01T00:00:00Z",
                finished_at: "2026-01-01T00:01:00Z",
              },
            ]),
            { status: 200 },
          );
        }
        return new Response(JSON.stringify([task({ id: "a" })]), {
          status: 200,
        });
      }),
    );

    await montar();
    const botao = screen.getByRole("button", {
      name: /histórico de execuções/i,
    });
    await act(async () => {
      botao.click();
    });

    expect(chamadas.some((c) => c.endsWith("/tasks/a/runs"))).toBe(true);
    expect(screen.getByText(/done · manual/i)).toBeInTheDocument();
  });

  it("histórico sem nenhuma execução mostra mensagem, não lista vazia silenciosa", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/runs")) {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        return new Response(JSON.stringify([task({ id: "a" })]), {
          status: 200,
        });
      }),
    );

    await montar();
    const botao = screen.getByRole("button", {
      name: /histórico de execuções/i,
    });
    await act(async () => {
      botao.click();
    });

    expect(screen.getByText(/nenhuma execução ainda/i)).toBeInTheDocument();
  });

  it("tarefa em triage/scheduled/review aparece — antes sumia do board", async () => {
    // Regressão: o board só declarava 5 colunas e o filtro de visibilidade
    // descartava qualquer status sem lane, então tarefas nesses três status
    // desapareciam sem nenhum aviso ao usuário.
    mockTasks([
      task({ id: "a", name: "em triagem", status: "triage" }),
      task({ id: "b", name: "agendada", status: "scheduled" }),
      task({ id: "c", name: "em revisão", status: "review" }),
    ]);

    await montar();

    expect(screen.getByText("em triagem")).toBeInTheDocument();
    expect(screen.getByText("agendada")).toBeInTheDocument();
    expect(screen.getByText("em revisão")).toBeInTheDocument();
  });

  it("erro/borda: status desconhecido cai na lane de fallback em vez de sumir", async () => {
    mockTasks([
      task({ id: "a", name: "status novo do backend", status: "quantum" }),
    ]);

    await montar();

    expect(screen.getByText("status novo do backend")).toBeInTheDocument();
    expect(screen.getByTestId("kanban-col-__other__")).toBeInTheDocument();
  });

  it("archived continua escondida por padrão e aparece com o filtro", async () => {
    mockTasks([task({ id: "b", name: "arquivada", status: "archived" })]);

    await montar();
    expect(screen.queryByText("arquivada")).not.toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByLabelText(/arquivadas/i));
    });
    expect(screen.getByText("arquivada")).toBeInTheDocument();
  });

  it("board vazio mostra todas as lanes expandidas (nenhuma colapsa sem trabalho)", async () => {
    mockTasks([]);

    await montar();

    for (const status of ["triage", "todo", "ready", "running", "done"]) {
      expect(screen.getByTestId(`kanban-col-${status}`)).toHaveAttribute(
        "data-collapsed",
        "false",
      );
    }
    expect(screen.getByText(/nenhuma tarefa/i)).toBeInTheDocument();
  });

  it("com trabalho no board, lane vazia colapsa em trilho e a preenchida fica expandida", async () => {
    mockTasks([task({ id: "a", name: "só essa", status: "todo" })]);

    await montar();

    expect(screen.getByTestId("kanban-col-todo")).toHaveAttribute(
      "data-collapsed",
      "false",
    );
    expect(screen.getByTestId("kanban-col-done")).toHaveAttribute(
      "data-collapsed",
      "true",
    );
  });

  it("linha de lanes quebra em grid (flex-wrap), sem depender de scroll horizontal", async () => {
    // Regressão do commit a08593bf: ele só MOVEU o overflow-x-auto do
    // container raiz pro container de lanes — nunca eliminou o scroll
    // horizontal nem adicionou quebra de linha real.
    mockTasks([task({ id: "a", status: "todo" })]);

    await montar();

    const linhaDeLanes = screen.getByTestId("kanban-col-todo").parentElement;
    expect(linhaDeLanes).not.toHaveClass("overflow-x-auto");
    expect(linhaDeLanes).toHaveClass("flex-wrap");
  });

  it("lane colapsada manualmente reexpande sozinha ao receber uma tarefa nova (self-heal)", async () => {
    mockTasks([task({ id: "a", status: "todo" })]);

    await montar();

    // "done" está vazia com o board tendo trabalho em "todo" — colapsa
    // automaticamente. Expande manualmente (override), depois confirma que
    // uma tarefa nova em "done" reexpande sozinha, sem exigir novo clique.
    expect(screen.getByTestId("kanban-col-done")).toHaveAttribute(
      "data-collapsed",
      "true",
    );
    fireEvent.click(screen.getByTestId("kanban-col-done"));
    expect(screen.getByTestId("kanban-col-done")).toHaveAttribute(
      "data-collapsed",
      "false",
    );
    fireEvent.click(
      within(screen.getByTestId("kanban-col-done")).getByRole("button", {
        name: /recolher/i,
      }),
    );
    expect(screen.getByTestId("kanban-col-done")).toHaveAttribute(
      "data-collapsed",
      "true",
    );

    // Override manual ainda ativo (colapsada) — chega uma tarefa nova via
    // SSE. O self-heal precisa descartar o override quando a fase muda de
    // vazia pra não-vazia, senão a tarefa fica escondida sem o usuário
    // perceber.
    mockTasks([
      task({ id: "a", status: "todo" }),
      task({ id: "b", status: "done" }),
    ]);
    const es = MockEventSource.instances[0]!;
    await act(async () => {
      es.emit({
        provider: "kanban",
        data: { task_id: "b", status: "done" },
      });
    });

    expect(screen.getByTestId("kanban-col-done")).toHaveAttribute(
      "data-collapsed",
      "false",
    );
  });

  it("card usa a cor de tone do próprio status na borda esquerda, não uma cor fixa", async () => {
    mockTasks([task({ id: "a", name: "tarefa bloqueada", status: "blocked" })]);

    await montar();

    const card = screen.getByText("tarefa bloqueada").closest("[style]");
    expect(card).toHaveStyle({
      borderLeftColor: "var(--color-kanban-tone-blocked)",
    });
  });

  it("chip de prioridade só aparece quando a prioridade não é 'normal'", async () => {
    mockTasks([
      task({
        id: "a",
        name: "tarefa urgente",
        status: "todo",
        priority: "urgent",
      }),
      task({
        id: "b",
        name: "tarefa comum",
        status: "todo",
        priority: "normal",
      }),
    ]);

    await montar();

    expect(screen.getByText("urgent")).toBeInTheDocument();
    // Erro/borda: prioridade "normal" é ruído em todo card — não deve
    // renderizar chip nenhum pra ela.
    expect(screen.queryByText("normal")).not.toBeInTheDocument();
  });

  it("botão Desbloquear só aparece em cards blocked e chama /unblock", async () => {
    const chamadas: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        chamadas.push(`${init?.method ?? "GET"} ${url}`);
        return new Response(
          JSON.stringify(
            init?.method === "POST"
              ? []
              : [task({ id: "a", status: "blocked", block_reason: "x" })],
          ),
          { status: 200 },
        );
      }),
    );

    await montar();
    const botao = screen.getByRole("button", { name: /desbloquear/i });
    await act(async () => {
      botao.click();
    });

    expect(
      chamadas.some((c) => c.startsWith("POST") && c.includes("/unblock")),
    ).toBe(true);
  });

  it("botão Cancelar não aparece em cards done, mas aparece nos demais", async () => {
    mockTasks([
      task({ id: "a", status: "todo" }),
      task({ id: "b", status: "done" }),
    ]);

    await montar();

    const colunaTodo = screen.getByTestId("kanban-col-todo");
    const colunaDone = screen.getByTestId("kanban-col-done");
    expect(
      within(colunaTodo).getByRole("button", { name: /cancelar/i }),
    ).toBeInTheDocument();
    expect(
      within(colunaDone).queryByRole("button", { name: /cancelar/i }),
    ).not.toBeInTheDocument();
  });

  it("Nova tarefa envia POST com nome/instrução e recarrega o board", async () => {
    const chamadas: { url: string; method: string; body?: string }[] = [];
    mockTasks([]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        chamadas.push({
          url,
          method: init?.method ?? "GET",
          body: init?.body as string | undefined,
        });
        return new Response(JSON.stringify([]), { status: 200 });
      }),
    );

    await montar();
    await act(async () => {
      screen.getByRole("button", { name: /nova tarefa/i }).click();
    });
    const nomeInput = screen.getByLabelText(/nome/i) as HTMLInputElement;
    const instrucaoInput = screen.getByLabelText(
      /instru[çc][ãa]o/i,
    ) as HTMLTextAreaElement;
    await act(async () => {
      fireEvent.change(nomeInput, { target: { value: "verificar deploy" } });
      fireEvent.change(instrucaoInput, {
        target: { value: "confira o status do deploy" },
      });
    });
    await act(async () => {
      screen.getByRole("button", { name: /^criar$/i }).click();
    });

    const post = chamadas.find(
      (c) => c.method === "POST" && c.url.includes("/background/tasks"),
    );
    expect(post).toBeTruthy();
    const corpo = JSON.parse(post?.body ?? "{}");
    expect(corpo).toMatchObject({
      name: "verificar deploy",
      instruction: "confira o status do deploy",
      trigger_type: "manual",
    });
  });

  it("Nova tarefa envia priority/workspace/assignee escolhidos, carregados de /workspaces e /agent-profiles", async () => {
    const chamadas: { url: string; method: string; body?: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        chamadas.push({
          url,
          method: init?.method ?? "GET",
          body: init?.body as string | undefined,
        });
        if (url === "/workspaces") {
          return new Response(
            JSON.stringify({
              workspaces: [{ id: "ws1", name: "Meu workspace" }],
            }),
            { status: 200 },
          );
        }
        if (url === "/agent-profiles") {
          return new Response(
            JSON.stringify([{ id: "ap1", name: "Backend agent" }]),
            { status: 200 },
          );
        }
        return new Response(JSON.stringify([]), { status: 200 });
      }),
    );

    await montar();
    await act(async () => {
      screen.getByRole("button", { name: /nova tarefa/i }).click();
    });
    // Erro/borda: as duas listas vêm de endpoints reais (/workspaces,
    // /agent-profiles), não são inventadas no frontend.
    await screen.findByRole("option", { name: "Meu workspace" });
    await screen.findByRole("option", { name: "Backend agent" });

    fireEvent.change(screen.getByLabelText(/nome/i), {
      target: { value: "tarefa com metadados" },
    });
    fireEvent.change(screen.getByLabelText(/instru[çc][ãa]o/i), {
      target: { value: "faça algo" },
    });
    fireEvent.change(screen.getByLabelText(/^prioridade$/i), {
      target: { value: "urgent" },
    });
    fireEvent.change(screen.getByLabelText(/^workspace$/i), {
      target: { value: "ws1" },
    });
    fireEvent.change(screen.getByLabelText(/respons[áa]vel/i), {
      target: { value: "ap1" },
    });
    await act(async () => {
      screen.getByRole("button", { name: /^criar$/i }).click();
    });

    const post = chamadas.find(
      (c) => c.method === "POST" && c.url.includes("/background/tasks"),
    );
    expect(post).toBeTruthy();
    expect(JSON.parse(post?.body ?? "{}")).toMatchObject({
      priority: "urgent",
      workspace_id: "ws1",
      agent_profile_id: "ap1",
    });
  });

  it("Nova tarefa com nome vazio não envia POST", async () => {
    const chamadas: string[] = [];
    mockTasks([]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "POST") chamadas.push(url);
        return new Response(JSON.stringify([]), { status: 200 });
      }),
    );

    await montar();
    await act(async () => {
      screen.getByRole("button", { name: /nova tarefa/i }).click();
    });
    await act(async () => {
      screen.getByRole("button", { name: /^criar$/i }).click();
    });

    expect(chamadas).toEqual([]);
  });

  it('botão "Rodar agora" só aparece em cards ready e chama /run', async () => {
    const chamadas: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        chamadas.push(`${init?.method ?? "GET"} ${url}`);
        return new Response(
          JSON.stringify(
            init?.method === "POST"
              ? { status: "queued" }
              : [task({ id: "a", status: "ready" })],
          ),
          { status: 200 },
        );
      }),
    );

    await montar();
    const botao = screen.getByRole("button", { name: /rodar agora/i });
    await act(async () => {
      botao.click();
    });

    expect(
      chamadas.some((c) => c.startsWith("POST") && c.endsWith("/run")),
    ).toBe(true);
  });

  it('"Rodar agora" não aparece fora do status ready', async () => {
    mockTasks([task({ id: "a", status: "todo" })]);

    await montar();

    expect(
      screen.queryByRole("button", { name: /rodar agora/i }),
    ).not.toBeInTheDocument();
  });

  it("falha ao carregar mostra board vazio, não tela de erro", async () => {
    // Erro/borda: as tarefas seguem rodando — só a visualização não carregou.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("rede fora");
      }),
    );

    await montar();

    expect(screen.getByText(/nenhuma tarefa/i)).toBeInTheDocument();
  });

  it("faz polling de reconciliação (baixa frequência) enquanto a aba está visível", async () => {
    vi.useFakeTimers();
    let chamadas = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        chamadas += 1;
        return new Response(JSON.stringify([]), { status: 200 });
      }),
    );
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {
      await Promise.resolve();
    });
    const antes = chamadas;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });

    expect(chamadas).toBeGreaterThanOrEqual(antes + 2);
    vi.useRealTimers();
  });

  it("não faz polling com a aba oculta", async () => {
    vi.useFakeTimers();
    let chamadas = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        chamadas += 1;
        return new Response(JSON.stringify([]), { status: 200 });
      }),
    );
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {
      await Promise.resolve();
    });
    const antes = chamadas;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });

    // Erro/borda: aba oculta não deve gerar chamada nova nenhuma —
    // só o fetch inicial do mount conta.
    expect(chamadas).toBe(antes);
    vi.useRealTimers();
  });

  it("unmount limpa o interval sem chamadas órfãs", async () => {
    vi.useFakeTimers();
    let chamadas = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        chamadas += 1;
        return new Response(JSON.stringify([]), { status: 200 });
      }),
    );
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });

    const { unmount } = render(<KanbanBoard threadId="s1" />);
    await act(async () => {
      await Promise.resolve();
    });

    unmount();
    const apos = chamadas;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });

    expect(chamadas).toBe(apos);
    vi.useRealTimers();
  });

  it("evento SSE de status atualiza só o card afetado, sem refazer o fetch do board", async () => {
    let chamadasFetch = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        chamadasFetch += 1;
        return new Response(
          JSON.stringify([task({ id: "a", status: "todo" })]),
          { status: 200 },
        );
      }),
    );

    await montar();
    expect(
      within(screen.getByTestId("kanban-col-todo")).getByText("tarefa"),
    ).toBeInTheDocument();
    const chamadasAntesDoEvento = chamadasFetch;

    const es = MockEventSource.instances[0];
    await act(async () => {
      es.emit({
        type: "webhook_event",
        provider: "kanban",
        event_type: "kanban_task.status_changed",
        data: {
          task_id: "a",
          status: "running",
          block_kind: null,
          block_reason: null,
        },
      });
    });

    expect(
      within(screen.getByTestId("kanban-col-running")).getByText("tarefa"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("kanban-col-todo")).queryByText("tarefa"),
    ).not.toBeInTheDocument();
    // Erro/borda: mover um card não é rebuscar o board inteiro — nenhum
    // fetch novo acontece só por causa do evento SSE.
    expect(chamadasFetch).toBe(chamadasAntesDoEvento);
  });

  it("evento SSE de provider diferente de kanban é ignorado", async () => {
    mockTasks([task({ id: "a", status: "todo" })]);

    await montar();
    const es = MockEventSource.instances[0];
    await act(async () => {
      es.emit({
        type: "webhook_event",
        provider: "github",
        event_type: "push",
        data: {
          task_id: "a",
          status: "running",
          block_kind: null,
          block_reason: null,
        },
      });
    });

    expect(
      within(screen.getByTestId("kanban-col-todo")).getByText("tarefa"),
    ).toBeInTheDocument();
  });

  it("reconexão do SSE depois de queda simulada volta a funcionar", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify([task({ id: "a", status: "todo" })]), {
            status: 200,
          }),
      ),
    );

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(MockEventSource.instances).toHaveLength(1);
    const primeira = MockEventSource.instances[0];

    await act(async () => {
      primeira.fail();
    });
    expect(primeira.closed).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(MockEventSource.instances).toHaveLength(2);
    const segunda = MockEventSource.instances[1];

    await act(async () => {
      segunda.emit({
        type: "webhook_event",
        provider: "kanban",
        event_type: "kanban_task.status_changed",
        data: {
          task_id: "a",
          status: "done",
          block_kind: null,
          block_reason: null,
        },
      });
    });

    expect(
      within(screen.getByTestId("kanban-col-done")).getByText("tarefa"),
    ).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("drag de todo pra ready chama o mesmo PATCH de status que a promoção manual usaria", async () => {
    const chamadas: { url: string; method: string; body?: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        chamadas.push({
          url,
          method: init?.method ?? "GET",
          body: init?.body as string | undefined,
        });
        return new Response(JSON.stringify({}), { status: 200 });
      }),
    );

    const aplicado = await applyDragTransition(
      "s1",
      task({ id: "a", status: "todo" }),
      "ready",
    );

    expect(aplicado).toBe(true);
    expect(chamadas).toHaveLength(1);
    expect(chamadas[0].method).toBe("PATCH");
    expect(chamadas[0].url).toBe("/sessions/s1/background/tasks/a");
    expect(JSON.parse(chamadas[0].body ?? "{}")).toEqual({ status: "ready" });
  });

  it("drag de blocked pra ready reaproveita o endpoint /unblock do botão", async () => {
    const chamadas: { url: string; method: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        chamadas.push({ url, method: init?.method ?? "GET" });
        return new Response(JSON.stringify({}), { status: 200 });
      }),
    );

    const aplicado = await applyDragTransition(
      "s1",
      task({ id: "a", status: "blocked" }),
      "ready",
    );

    expect(aplicado).toBe(true);
    expect(chamadas).toEqual([
      { url: "/sessions/s1/background/tasks/a/unblock", method: "POST" },
    ]);
  });

  it("drag de ready pra done é recusado — nenhuma chamada de API acontece", async () => {
    // Erro/borda de regressão: `*→done` é exclusivo da run terminando de
    // verdade — arrastar o card nunca pode ter efeito, mesmo no frontend.
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({}), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const aplicado = await applyDragTransition(
      "s1",
      task({ id: "a", status: "ready" }),
      "done",
    );

    expect(aplicado).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("drag de todo pra running é recusado — nenhuma chamada de API acontece", async () => {
    // Erro/borda: `*→running` é exclusivo do claim atômico do scheduler.
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({}), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const aplicado = await applyDragTransition(
      "s1",
      task({ id: "a", status: "todo" }),
      "running",
    );

    expect(aplicado).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("marcar 2 cards e clicar Arquivar dispara o endpoint bulk com os ids certos", async () => {
    const chamadas: { url: string; method: string; body?: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        chamadas.push({
          url,
          method: init?.method ?? "GET",
          body: init?.body as string | undefined,
        });
        return new Response(
          JSON.stringify(
            init?.method === "PATCH"
              ? []
              : [
                  task({ id: "a", name: "tarefa a", status: "todo" }),
                  task({ id: "b", name: "tarefa b", status: "todo" }),
                  task({ id: "c", name: "tarefa c", status: "todo" }),
                ],
          ),
          { status: 200 },
        );
      }),
    );

    await montar();
    const checkboxes = screen.getAllByLabelText(/selecionar tarefa/i);
    await act(async () => {
      fireEvent.click(checkboxes[0]);
      fireEvent.click(checkboxes[1]);
    });
    await act(async () => {
      screen.getByRole("button", { name: /arquivar/i }).click();
    });

    const bulk = chamadas.find((c) => c.url.endsWith("/tasks/bulk"));
    expect(bulk).toBeTruthy();
    expect(bulk?.method).toBe("PATCH");
    expect(JSON.parse(bulk?.body ?? "{}")).toEqual({
      task_ids: ["a", "b"],
      action: "archive",
    });
  });

  it("barra de seleção não aparece sem cards marcados", async () => {
    mockTasks([task({ id: "a", status: "todo" })]);

    await montar();

    expect(
      screen.queryByRole("button", { name: /arquivar/i }),
    ).not.toBeInTheDocument();
  });

  it("unmount fecha a conexão SSE ativa", async () => {
    mockTasks([task({ id: "a", status: "todo" })]);

    const { unmount } = render(<KanbanBoard threadId="s1" />);
    await act(async () => {
      await Promise.resolve();
    });
    const es = MockEventSource.instances[0];

    unmount();

    expect(es.closed).toBe(true);
  });

  it("card mostra prioridade não-normal, tenant e assignee quando presentes", async () => {
    mockTasks([
      task({
        id: "a",
        status: "todo",
        priority: "urgent",
        workspace_id: "ws-1",
        agent_profile_id: "profile-x",
      }),
    ]);

    await montar();

    expect(screen.getByText("urgent")).toBeInTheDocument();
    expect(screen.getByText("workspace: ws-1")).toBeInTheDocument();
    expect(screen.getByText("perfil: profile-x")).toBeInTheDocument();
  });

  it("prioridade 'normal' não aparece como badge (ruído visual desnecessário)", async () => {
    mockTasks([task({ id: "a", status: "todo", priority: "normal" })]);

    await montar();

    expect(screen.queryByText("normal")).not.toBeInTheDocument();
  });

  it("filtro de busca esconde cards que não casam com o nome", async () => {
    mockTasks([
      task({ id: "a", name: "corrigir bug do login", status: "todo" }),
      task({ id: "b", name: "atualizar docs", status: "todo" }),
    ]);

    await montar();
    const busca = screen.getByPlaceholderText(/buscar por nome/i);
    fireEvent.change(busca, { target: { value: "bug" } });

    expect(screen.getByText("corrigir bug do login")).toBeInTheDocument();
    expect(screen.queryByText("atualizar docs")).not.toBeInTheDocument();
  });

  it("filtro de tenant restringe aos cards do workspace escolhido", async () => {
    mockTasks([
      task({
        id: "a",
        name: "tarefa ws1",
        status: "todo",
        workspace_id: "ws-1",
      }),
      task({
        id: "b",
        name: "tarefa ws2",
        status: "todo",
        workspace_id: "ws-2",
      }),
    ]);

    await montar();
    const seletor = screen.getByLabelText(/todos os workspaces/i);
    fireEvent.change(seletor, { target: { value: "ws-1" } });

    expect(screen.getByText("tarefa ws1")).toBeInTheDocument();
    expect(screen.queryByText("tarefa ws2")).not.toBeInTheDocument();
  });

  it("toggle 'mostrar arquivadas' revela a coluna archived", async () => {
    mockTasks([task({ id: "a", name: "arquivada", status: "archived" })]);

    await montar();
    expect(screen.queryByTestId("kanban-col-archived")).not.toBeInTheDocument();

    const toggle = screen.getByLabelText(/mostrar arquivadas/i);
    fireEvent.click(toggle);

    expect(screen.getByTestId("kanban-col-archived")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("kanban-col-archived")).getByText("arquivada"),
    ).toBeInTheDocument();
  });
});

describe("KanbanBoard — multi-board", () => {
  it("switcher lista os boards do usuário e a opção 'board da sessão'", async () => {
    mockMultiBoard({
      boards: [{ id: "b1", slug: "b1", name: "Board Alfa" }],
    });

    await montar();

    const select = screen.getByLabelText(/board ativo/i) as HTMLSelectElement;
    const opcoes = Array.from(select.options).map((o) => o.textContent);
    expect(opcoes).toEqual(["Board da sessão", "Board Alfa"]);
  });

  it("trocar pra um board busca /boards/{id}/board e mostra as tasks agrupadas", async () => {
    mockMultiBoard({
      boards: [{ id: "b1", slug: "b1", name: "Board Alfa" }],
      boardView: {
        columns: [
          { status: "todo", tasks: [task({ id: "t1", name: "do board" })] },
        ],
      },
      sessionTasks: [task({ id: "t2", name: "da sessão" })],
    });

    await montar();
    // Sem board ativo: mostra a task da session.
    expect(screen.getByText("da sessão")).toBeInTheDocument();

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/board ativo/i), {
        target: { value: "b1" },
      });
    });

    // Com o board ativo: mostra a task do board, não mais a da session.
    expect(await screen.findByText("do board")).toBeInTheDocument();
    expect(screen.queryByText("da sessão")).not.toBeInTheDocument();
  });

  it("criar um board novo o seleciona automaticamente", async () => {
    const chamadas: { url: string; method: string; body?: string }[] = [];
    mockMultiBoard(
      {
        boards: [],
        boardView: { columns: [] },
      },
      chamadas,
    );

    await montar();
    await act(async () => {
      screen.getByRole("button", { name: /\+ novo board/i }).click();
    });
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/nome do board/i), {
        target: { value: "Board X" },
      });
    });
    await act(async () => {
      screen.getByRole("button", { name: /^criar$/i }).click();
    });

    const post = chamadas.find(
      (c) => c.method === "POST" && c.url.endsWith("/boards"),
    );
    expect(post).toBeTruthy();
    expect(JSON.parse(post?.body ?? "{}")).toMatchObject({ name: "Board X" });

    const select =
      await screen.findByLabelText<HTMLSelectElement>(/board ativo/i);
    expect(select.value).toBe("board-novo");
  });

  it("nova tarefa criada com um board ativo envia board_id no POST", async () => {
    const chamadas: { url: string; method: string; body?: string }[] = [];
    mockMultiBoard(
      {
        boards: [{ id: "b1", slug: "b1", name: "Board Alfa" }],
        boardView: { columns: [] },
      },
      chamadas,
    );

    await montar();
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/board ativo/i), {
        target: { value: "b1" },
      });
    });
    await act(async () => {
      screen.getByRole("button", { name: /nova tarefa/i }).click();
    });
    fireEvent.change(screen.getByLabelText(/^nome$/i), {
      target: { value: "tarefa do board" },
    });
    fireEvent.change(screen.getByLabelText(/instru[çc][ãa]o/i), {
      target: { value: "faça algo" },
    });
    await act(async () => {
      screen.getByRole("button", { name: /^criar$/i }).click();
    });

    const post = chamadas.find(
      (c) => c.method === "POST" && c.url.endsWith("/background/tasks"),
    );
    expect(post).toBeTruthy();
    expect(JSON.parse(post?.body ?? "{}")).toMatchObject({
      board_id: "b1",
    });
  });
});

describe("arcState", () => {
  it("running com heartbeat fresco (<120s desde o último) → running", () => {
    const daqui900s = new Date(Date.now() + 900_000).toISOString();
    expect(
      arcState(task({ status: "running", claim_expires_at: daqui900s })),
    ).toBe("running");
  });

  it("running sem claim_expires_at → stale, nunca running (sem dado, sem decoração)", () => {
    // Erro/borda: o mandato desta fase é nunca fingir "ao vivo" sem prova —
    // ausência do campo (backend antigo, ou task migrada) cai pro lado
    // pessimista, não pro lado bonito.
    expect(arcState(task({ status: "running", claim_expires_at: null }))).toBe(
      "stale",
    );
  });

  it("running com heartbeat vencido há mais de 120s → stale", () => {
    // TTL total é 900s; heartbeat que não renovou nos últimos 120s deixa
    // menos de 780s de claim restante.
    const daqui700s = new Date(Date.now() + 700_000).toISOString();
    expect(
      arcState(task({ status: "running", claim_expires_at: daqui700s })),
    ).toBe("stale");
  });

  it.each(["review", "triage"])("%s → queued (aguardando alguém)", (status) => {
    expect(arcState(task({ status }))).toBe("queued");
  });

  it("ready com assignee → queued", () => {
    expect(
      arcState(task({ status: "ready", agent_profile_id: "perfil-1" })),
    ).toBe("queued");
  });

  it("ready sem assignee → none (erro/borda: ninguém esperando por ela ainda)", () => {
    expect(arcState(task({ status: "ready" }))).toBe("none");
  });

  it.each(["todo", "blocked", "done", "archived"])("%s → none", (status) => {
    expect(arcState(task({ status }))).toBe("none");
  });
});
