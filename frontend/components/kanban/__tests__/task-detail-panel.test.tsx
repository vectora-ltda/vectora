// @vitest-environment jsdom
/**
 * Painel de detalhe do card do Kanban — comentários + timeline de
 * transições, aberto pelo botão "Ver detalhes" no `TaskCard`.
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
import { KanbanBoard } from "../kanban-board";

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

class MockEventSource {
  static instances: MockEventSource[] = [];
  closed = false;
  constructor(public url: string) {
    MockEventSource.instances.push(this);
  }
  addEventListener() {}
  close() {
    this.closed = true;
  }
}

beforeEach(() => {
  overwriteGetLocale(() => "pt");
  vi.stubGlobal("EventSource", MockEventSource);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  overwriteGetLocale(() => baseLocale);
});

async function abrirDetalhes() {
  const botao = screen.getByRole("button", { name: /ver detalhes/i });
  await act(async () => {
    botao.click();
  });
}

/** Stub de fetch cobrindo tudo que o drawer toca — tasks,
 * comentários/eventos vazios por padrão, /agent-profiles, /links,
 * /review/approve, e qualquer PATCH em /tasks/{id}. Registra cada
 * chamada em `chamadas` (se fornecido) pra os testes inspecionarem
 * método/corpo. */
function mockPanelFetches(
  tasks: unknown[],
  chamadas?: { url: string; method: string; body?: string }[],
  opts: {
    profiles?: { id: string; name: string }[];
    linkStatus?: number;
  } = {},
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      chamadas?.push({ url, method, body: init?.body as string | undefined });

      if (url.endsWith("/comments")) return new Response("[]", { status: 200 });
      if (url.endsWith("/events")) return new Response("[]", { status: 200 });
      if (url.endsWith("/agent-profiles")) {
        return new Response(JSON.stringify(opts.profiles ?? []), {
          status: 200,
        });
      }
      if (url.includes("/links") && method === "POST") {
        return new Response("{}", { status: opts.linkStatus ?? 201 });
      }
      if (url.includes("/links/") && method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/review/approve") && method === "POST") {
        return new Response(JSON.stringify(tasks[0]), { status: 200 });
      }
      if (method === "PATCH") {
        return new Response(JSON.stringify(tasks[0]), { status: 200 });
      }
      return new Response(JSON.stringify(tasks), { status: 200 });
    }),
  );
}

describe("TaskDetailPanel", () => {
  it("abre o painel, busca comentários/eventos sob demanda e lista o resultado", async () => {
    const chamadas: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        chamadas.push(url);
        if (url.endsWith("/comments")) {
          return new Response(
            JSON.stringify([
              {
                id: "c1",
                user_id: "u1",
                body: "primeiro comentário",
                created_at: "2026-01-01T00:00:00Z",
              },
            ]),
            { status: 200 },
          );
        }
        if (url.endsWith("/events")) {
          return new Response(
            JSON.stringify([
              {
                id: "e1",
                from_status: "todo",
                to_status: "ready",
                block_kind: null,
                block_reason: null,
                created_at: "2026-01-01T00:00:00Z",
              },
            ]),
            { status: 200 },
          );
        }
        return new Response(JSON.stringify([task()]), { status: 200 });
      }),
    );

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {});
    await abrirDetalhes();

    expect(chamadas.some((c) => c.endsWith("/tasks/t1/comments"))).toBe(true);
    expect(chamadas.some((c) => c.endsWith("/tasks/t1/events"))).toBe(true);
    expect(screen.getByText("primeiro comentário")).toBeInTheDocument();
    expect(screen.getByText(/todo → ready/i)).toBeInTheDocument();
  });

  it("enviar comentário faz POST e recarrega a lista", async () => {
    const chamadas: { url: string; method: string; body?: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        chamadas.push({
          url,
          method: init?.method ?? "GET",
          body: init?.body as string | undefined,
        });
        if (url.endsWith("/comments") && init?.method === "POST") {
          return new Response(
            JSON.stringify({
              id: "c1",
              user_id: "u1",
              body: "novo comentário",
              created_at: "2026-01-01T00:00:00Z",
            }),
            { status: 201 },
          );
        }
        if (url.endsWith("/comments"))
          return new Response("[]", { status: 200 });
        if (url.endsWith("/events")) return new Response("[]", { status: 200 });
        return new Response(JSON.stringify([task()]), { status: 200 });
      }),
    );

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {});
    await abrirDetalhes();

    const textarea = screen.getByLabelText(/escreva um comentário/i);
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "novo comentário" } });
    });
    await act(async () => {
      screen.getByRole("button", { name: /^comentar$/i }).click();
    });

    const post = chamadas.find(
      (c) => c.method === "POST" && c.url.endsWith("/comments"),
    );
    expect(post).toBeTruthy();
    expect(JSON.parse(post?.body ?? "{}")).toEqual({ body: "novo comentário" });
  });

  it("falha de rede ao comentar mostra erro discreto, sem quebrar o painel", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/comments") && init?.method === "POST") {
          return new Response("erro", { status: 500 });
        }
        if (url.endsWith("/comments"))
          return new Response("[]", { status: 200 });
        if (url.endsWith("/events")) return new Response("[]", { status: 200 });
        return new Response(JSON.stringify([task()]), { status: 200 });
      }),
    );

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {});
    await abrirDetalhes();

    const textarea = screen.getByLabelText(/escreva um comentário/i);
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "vai falhar" } });
    });
    await act(async () => {
      screen.getByRole("button", { name: /^comentar$/i }).click();
    });

    expect(
      await screen.findByText(/não foi possível enviar o comentário/i),
    ).toBeInTheDocument();
    // O painel segue funcional — o título do card continua visível.
    expect(screen.getAllByText("tarefa").length).toBeGreaterThan(0);
  });

  it("painel vazio mostra as mensagens de 'nenhum comentário'/'nenhuma mudança'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/comments"))
          return new Response("[]", { status: 200 });
        if (url.endsWith("/events")) return new Response("[]", { status: 200 });
        return new Response(JSON.stringify([task()]), { status: 200 });
      }),
    );

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {});
    await abrirDetalhes();

    expect(
      await screen.findByText(/nenhum comentário ainda/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/nenhuma mudança de status ainda/i),
    ).toBeInTheDocument();
  });

  it("menu de status só oferece os alvos legais de DRAG_TRANSITIONS", async () => {
    // Task em "todo" só pode ir pra ready/triage manualmente
    // (running/done são exclusivos do claim/run real).
    mockPanelFetches([task({ status: "todo" })]);

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {});
    await abrirDetalhes();

    const select = screen.getByLabelText(/^status$/i) as HTMLSelectElement;
    const opcoes = Array.from(select.options).map((o) => o.value);
    expect(opcoes).toEqual(["todo", "ready", "triage"]);
  });

  it("trocar o status no menu envia PATCH com o status escolhido", async () => {
    const chamadas: { url: string; method: string; body?: string }[] = [];
    mockPanelFetches([task({ status: "todo" })], chamadas);

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {});
    await abrirDetalhes();

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/^status$/i), {
        target: { value: "ready" },
      });
    });

    const patch = chamadas.find(
      (c) => c.method === "PATCH" && c.url.endsWith("/tasks/t1"),
    );
    expect(patch).toBeTruthy();
    expect(JSON.parse(patch?.body ?? "{}")).toMatchObject({ status: "ready" });
  });

  it("task em review mostra botões aprovar/reprovar; aprovar chama o endpoint dedicado", async () => {
    const chamadas: { url: string; method: string }[] = [];
    mockPanelFetches([task({ status: "review" })], chamadas);

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {});
    await abrirDetalhes();

    await act(async () => {
      screen.getByRole("button", { name: /^aprovar$/i }).click();
    });

    expect(
      chamadas.some(
        (c) => c.method === "POST" && c.url.endsWith("/review/approve"),
      ),
    ).toBe(true);
  });

  it("reprovar review usa o PATCH genérico de volta pra ready", async () => {
    const chamadas: { url: string; method: string; body?: string }[] = [];
    mockPanelFetches([task({ status: "review" })], chamadas);

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {});
    await abrirDetalhes();

    await act(async () => {
      screen.getByRole("button", { name: /^reprovar$/i }).click();
    });

    const patch = chamadas.find(
      (c) => c.method === "PATCH" && c.url.endsWith("/tasks/t1"),
    );
    expect(JSON.parse(patch?.body ?? "{}")).toMatchObject({ status: "ready" });
  });

  it("assignee carrega de /agent-profiles e trocar envia PATCH", async () => {
    const chamadas: { url: string; method: string; body?: string }[] = [];
    mockPanelFetches([task({ status: "todo" })], chamadas, {
      profiles: [{ id: "ap1", name: "Backend agent" }],
    });

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {});
    await abrirDetalhes();

    await screen.findByRole("option", { name: "Backend agent" });
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/responsável/i), {
        target: { value: "ap1" },
      });
    });

    const patch = chamadas.find(
      (c) => c.method === "PATCH" && c.url.endsWith("/tasks/t1"),
    );
    expect(JSON.parse(patch?.body ?? "{}")).toMatchObject({
      agent_profile_id: "ap1",
    });
  });

  it("barra de progresso aparece só quando task.progress existe", async () => {
    mockPanelFetches([
      task({ status: "todo", progress: { done: 1, total: 3 } }),
    ]);

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {});
    await abrirDetalhes();

    expect(screen.getByText("1/3")).toBeInTheDocument();
  });

  it("dependências: adiciona via POST /links e remove via DELETE", async () => {
    const chamadas: { url: string; method: string; body?: string }[] = [];
    mockPanelFetches(
      [
        task({
          status: "todo",
          dependencies: [{ id: "pai1", name: "Pai existente", status: "done" }],
        }),
      ],
      chamadas,
    );

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {});
    await abrirDetalhes();

    // "Pai existente" também aparece no contador N/M do próprio card —
    // escopa a query pro conteúdo do drawer (role="dialog"), não pro
    // documento inteiro.
    const drawer = within(screen.getByRole("dialog"));
    expect(drawer.getByText(/Pai existente/)).toBeInTheDocument();

    await act(async () => {
      fireEvent.change(drawer.getByLabelText(/id da task da qual depende/i), {
        target: { value: "pai2" },
      });
    });
    await act(async () => {
      drawer.getByRole("button", { name: /^adicionar$/i }).click();
    });
    expect(
      chamadas.some(
        (c) => c.method === "POST" && c.url.endsWith("/tasks/t1/links"),
      ),
    ).toBe(true);

    await act(async () => {
      drawer.getByRole("button", { name: /remover dependência/i }).click();
    });
    expect(
      chamadas.some(
        (c) => c.method === "DELETE" && c.url.endsWith("/links/pai1"),
      ),
    ).toBe(true);
  });

  it("erro/borda: ciclo ao adicionar dependência mostra erro sem quebrar o painel", async () => {
    mockPanelFetches([task({ status: "todo" })], undefined, {
      linkStatus: 409,
    });

    render(<KanbanBoard threadId="s1" />);
    await act(async () => {});
    await abrirDetalhes();

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/id da task da qual depende/i), {
        target: { value: "ciclo" },
      });
    });
    await act(async () => {
      screen.getByRole("button", { name: /^adicionar$/i }).click();
    });

    expect(
      await screen.findByText(/não foi possível adicionar/i),
    ).toBeInTheDocument();
  });
});
