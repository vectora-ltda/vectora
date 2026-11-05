// @vitest-environment jsdom
/**
 * RAG é escopo de workspace (LanceDB persiste entre sessões), não de thread
 * — uma sessão nova (sem ragCitations ainda) num workspace já indexado não
 * pode mostrar o mesmo "vazio" genérico de um workspace de verdade vazio.
 * A aba consulta GET /rag/workspace-summary pra distinguir os dois casos.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  cleanup,
  fireEvent,
} from "@testing-library/react";
import { MemoryTab } from "../memory-tab";

function bucketsFetchMock(
  buckets: {
    id: string;
    name: string;
    description_md: string;
    source_path: string | null;
    created_at: string;
    active: boolean;
  }[],
) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes("/rag/workspace-summary")) {
      return new Response(JSON.stringify({ collections: [] }));
    }
    if (String(url) === "/workspaces/ws-1/rag/buckets") {
      return new Response(JSON.stringify(buckets));
    }
    if (
      String(url).startsWith("/workspaces/ws-1/rag/buckets/") &&
      init?.method === "PATCH"
    ) {
      return new Response(JSON.stringify({ active: true }));
    }
    if (
      String(url).startsWith("/workspaces/ws-1/rag/buckets/") &&
      init?.method === "DELETE"
    ) {
      return new Response(JSON.stringify({ ok: true }));
    }
    throw new Error(`unmocked fetch: ${url}`);
  });
}

function stubFetch(journey: unknown, ok = true) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("/memory/journey")) {
        return ok
          ? new Response(JSON.stringify(journey))
          : new Response("erro", { status: 500 });
      }
      if (u.includes("/rag/workspace-summary")) {
        return new Response(JSON.stringify({ collections: [] }));
      }
      if (u.includes("/rag/buckets")) {
        return new Response(JSON.stringify([]));
      }
      throw new Error(`unmocked fetch: ${u}`);
    }),
  );
}

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get:
        (_t, prop) =>
        (...args: unknown[]) => {
          const params = args[0] as Record<string, unknown> | undefined;
          return params
            ? `${String(prop)}(${JSON.stringify(params)})`
            : String(prop);
        },
    },
  ),
}));

const mockMessages = vi.fn();
vi.mock("@/lib/hooks/chat/use-thread-messages", () => ({
  useThreadMessages: (threadId: string) => [mockMessages(threadId), vi.fn()],
}));

vi.mock("@/lib/stores/workspaces-store", () => ({
  useWorkspacesStore: (
    sel: (s: { getActive: () => { id: string } | undefined }) => unknown,
  ) => sel({ getActive: () => ({ id: "ws-1" }) }),
}));

vi.mock("@/lib/stores/rag-jobs-store", () => ({
  useRagJobsStore: (sel: (s: { jobs: Record<string, never> }) => unknown) =>
    sel({ jobs: {} }),
}));

vi.mock("@/components/workbench/rag-settings-panel", () => ({
  useRagSettings: () => ({
    open: false,
    toggle: vi.fn(),
    close: vi.fn(),
    settings: {},
    collections: [],
    patch: vi.fn(),
    loadCollections: vi.fn(),
    deleteCollection: vi.fn(),
  }),
  RagSettingsButton: () => <div data-testid="rag-settings-btn-stub" />,
  RagSettingsSlidePanel: () => <div data-testid="rag-settings-panel-stub" />,
}));

beforeEach(() => {
  mockMessages.mockReturnValue([]);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("MemoryTab", () => {
  it("mostra 'memória vazia' genérico quando o workspace nunca foi indexado (edge)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ collections: [] }))),
    );

    render(<MemoryTab threadId="t1" />);

    await waitFor(() =>
      expect(
        screen.getByText("workbench_memory_empty_title"),
      ).toBeInTheDocument(),
    );
  });

  it("mostra que já há conteúdo indexado quando o workspace tem RAG de sessão anterior", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/rag/workspace-summary")) {
          return new Response(
            JSON.stringify({
              collections: [
                { name: "articles", count: 3 },
                { name: "web_cache", count: 2 },
              ],
            }),
          );
        }
        if (String(url).includes("/rag/buckets")) {
          return new Response(JSON.stringify([]));
        }
        throw new Error(`unmocked fetch: ${url}`);
      }),
    );

    render(<MemoryTab threadId="t1" />);

    await waitFor(() =>
      expect(
        screen.getByText("workbench_memory_indexed_title"),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText('workbench_memory_indexed_desc({"n":5})'),
    ).toBeInTheDocument();
    // Não deve mostrar o "vazio" genérico junto.
    expect(
      screen.queryByText("workbench_memory_empty_title"),
    ).not.toBeInTheDocument();
  });

  it("chama /rag/workspace-summary com o workspace_id ativo", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ collections: [] })),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryTab threadId="t1" />);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/rag/workspace-summary?workspace_id=ws-1",
      ),
    );
  });

  it("tolera falha de rede no resumo do workspace e cai no vazio genérico (edge)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );

    render(<MemoryTab threadId="t1" />);

    await waitFor(() =>
      expect(
        screen.getByText("workbench_memory_empty_title"),
      ).toBeInTheDocument(),
    );
  });

  it("prioriza atividade/citações da thread atual sobre o resumo do workspace quando ambos existem", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({ collections: [{ name: "articles", count: 3 }] }),
          ),
      ),
    );
    mockMessages.mockReturnValue([
      {
        id: "m1",
        role: "assistant",
        content: "resposta",
        ragCitations: [
          { source: "doc.md", chunk: "trecho recuperado", index: 1 },
        ],
      },
    ]);

    render(<MemoryTab threadId="t1" />);

    await waitFor(() => expect(screen.getByText("doc.md")).toBeInTheDocument());
    expect(
      screen.queryByText("workbench_memory_indexed_title"),
    ).not.toBeInTheDocument();
  });

  it("busca /rag/search com o texto digitado e mostra os resultados", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).includes("/rag/workspace-summary")) {
        return new Response(JSON.stringify({ collections: [] }));
      }
      if (String(url) === "/rag/search") {
        const body = JSON.parse(String(init?.body));
        expect(body).toEqual({ query: "auth", workspace_id: "ws-1" });
        return new Response(
          JSON.stringify({
            results: [{ content: "trecho sobre auth", collection: "articles" }],
          }),
        );
      }
      if (String(url).includes("/memory/search")) {
        return new Response(JSON.stringify({ hits: [] }));
      }
      if (String(url).includes("/rag/buckets")) {
        return new Response(JSON.stringify([]));
      }
      throw new Error(`unmocked fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryTab threadId="t1" />);

    fireEvent.change(
      screen.getByPlaceholderText("workbench_memory_search_placeholder"),
      { target: { value: "auth" } },
    );

    await waitFor(
      () => expect(screen.getAllByText("articles").length).toBeGreaterThan(0),
      { timeout: 2000 },
    );
    fireEvent.click(screen.getByRole("button", { name: /articles/ }));
    expect(screen.getByText("trecho sobre auth")).toBeInTheDocument();
  });

  it("mostra 'nenhum resultado' quando a busca não encontra nada (edge)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/rag/workspace-summary")) {
          return new Response(JSON.stringify({ collections: [] }));
        }
        if (String(url) === "/rag/search") {
          return new Response(JSON.stringify({ results: [] }));
        }
        if (String(url).includes("/memory/search")) {
          return new Response(JSON.stringify({ hits: [] }));
        }
        if (String(url).includes("/rag/buckets")) {
          return new Response(JSON.stringify([]));
        }
        throw new Error(`unmocked fetch: ${url}`);
      }),
    );

    render(<MemoryTab threadId="t1" />);

    fireEvent.change(
      screen.getByPlaceholderText("workbench_memory_search_placeholder"),
      { target: { value: "nada-aqui" } },
    );

    await waitFor(
      () =>
        expect(
          screen.getAllByText("workbench_memory_search_no_results").length,
        ).toBeGreaterThan(0),
      { timeout: 2000 },
    );
  });

  describe("painel de buckets", () => {
    it("lista os buckets do workspace ativo", async () => {
      vi.stubGlobal(
        "fetch",
        bucketsFetchMock([
          {
            id: "b1",
            name: "Docs internos",
            description_md: "",
            source_path: "/x/docs",
            created_at: "2026-01-01T00:00:00Z",
            active: true,
          },
        ]),
      );

      render(<MemoryTab threadId="t1" />);

      await waitFor(() =>
        expect(screen.getByText("Docs internos")).toBeInTheDocument(),
      );
      expect(screen.getByText("/x/docs")).toBeInTheDocument();
    });

    it("erro/borda: workspace sem buckets mostra o estado vazio do painel", async () => {
      vi.stubGlobal("fetch", bucketsFetchMock([]));

      render(<MemoryTab threadId="t1" />);

      await waitFor(() =>
        expect(
          screen.getByText("workbench_memory_buckets_empty"),
        ).toBeInTheDocument(),
      );
    });

    it("alternar o switch de um bucket chama PATCH com o novo estado", async () => {
      const fetchMock = bucketsFetchMock([
        {
          id: "b1",
          name: "Docs internos",
          description_md: "",
          source_path: null,
          created_at: "2026-01-01T00:00:00Z",
          active: true,
        },
      ]);
      vi.stubGlobal("fetch", fetchMock);

      render(<MemoryTab threadId="t1" />);

      await waitFor(() =>
        expect(screen.getByText("Docs internos")).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole("switch"));

      await waitFor(() => {
        const calls = fetchMock.mock.calls;
        expect(
          calls.some(
            (c) =>
              c[0] === "/workspaces/ws-1/rag/buckets/b1" &&
              (c[1] as RequestInit)?.method === "PATCH",
          ),
        ).toBe(true);
      });
    });

    it("remover um bucket pede confirmação e chama DELETE", async () => {
      const fetchMock = bucketsFetchMock([
        {
          id: "b1",
          name: "Docs internos",
          description_md: "",
          source_path: null,
          created_at: "2026-01-01T00:00:00Z",
          active: true,
        },
      ]);
      vi.stubGlobal("fetch", fetchMock);
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

      render(<MemoryTab threadId="t1" />);

      await waitFor(() =>
        expect(screen.getByText("Docs internos")).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByLabelText("workbench_memory_buckets_remove"));

      await waitFor(() => {
        const calls = fetchMock.mock.calls;
        expect(
          calls.some(
            (c) =>
              c[0] === "/workspaces/ws-1/rag/buckets/b1" &&
              (c[1] as RequestInit)?.method === "DELETE",
          ),
        ).toBe(true);
      });
      confirmSpy.mockRestore();
    });

    it("erro/borda: cancelar a confirmação não chama DELETE", async () => {
      const fetchMock = bucketsFetchMock([
        {
          id: "b1",
          name: "Docs internos",
          description_md: "",
          source_path: null,
          created_at: "2026-01-01T00:00:00Z",
          active: true,
        },
      ]);
      vi.stubGlobal("fetch", fetchMock);
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

      render(<MemoryTab threadId="t1" />);

      await waitFor(() =>
        expect(screen.getByText("Docs internos")).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByLabelText("workbench_memory_buckets_remove"));

      const calls = fetchMock.mock.calls;
      expect(
        calls.some((c) => (c[1] as RequestInit)?.method === "DELETE"),
      ).toBe(false);
      confirmSpy.mockRestore();
    });
  });
});

function unifiedFetchMock(hits: unknown[]) {
  return vi.fn(async (url: string) => {
    if (String(url).includes("/rag/workspace-summary")) {
      return new Response(JSON.stringify({ collections: [] }));
    }
    if (String(url).includes("/memory/search")) {
      return new Response(JSON.stringify({ hits }));
    }
    if (String(url).includes("/rag/buckets")) {
      return new Response(JSON.stringify([]));
    }
    if (String(url) === "/rag/search") {
      return new Response(JSON.stringify({ results: [] }));
    }
    throw new Error(`unmocked fetch: ${url}`);
  });
}

describe("MemoryTab — busca unificada (fatos + skills + buckets)", () => {
  it("digitar na busca chama /memory/search e mostra fato+skill+bucket combinados", async () => {
    vi.stubGlobal(
      "fetch",
      unifiedFetchMock([
        { type: "fact", id: "f1", title: "f1", snippet: "conteúdo do fato" },
        { type: "skill", id: "s1", title: "Skill X", snippet: "descrição" },
        { type: "rag_bucket", id: "b1", title: "Bucket Y", snippet: "docs" },
      ]),
    );

    render(<MemoryTab threadId="t1" />);

    fireEvent.change(
      screen.getByPlaceholderText("workbench_memory_search_placeholder"),
      { target: { value: "godot" } },
    );

    await waitFor(() =>
      expect(screen.getByText("Skill X")).toBeInTheDocument(),
    );
    expect(screen.getByText("Bucket Y")).toBeInTheDocument();
    expect(screen.getByText("conteúdo do fato")).toBeInTheDocument();
  });

  it("erro/borda: falha de rede na busca unificada não derruba a aba (degrada pra vazio)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/rag/workspace-summary")) {
          return new Response(JSON.stringify({ collections: [] }));
        }
        if (String(url).includes("/memory/search")) {
          throw new Error("network down");
        }
        if (String(url).includes("/rag/buckets")) {
          return new Response(JSON.stringify([]));
        }
        if (String(url) === "/rag/search") {
          return new Response(JSON.stringify({ results: [] }));
        }
        throw new Error(`unmocked fetch: ${url}`);
      }),
    );

    render(<MemoryTab threadId="t1" />);

    fireEvent.change(
      screen.getByPlaceholderText("workbench_memory_search_placeholder"),
      { target: { value: "godot" } },
    );

    await waitFor(() =>
      expect(
        screen.getAllByText("workbench_memory_search_no_results").length,
      ).toBeGreaterThan(0),
    );
  });

  it("clicar num chip de tipo filtra a busca por esse tipo (fact,skill,rag_bucket)", async () => {
    const fetchMock = unifiedFetchMock([
      { type: "fact", id: "f1", title: "f1", snippet: "conteúdo" },
    ]);
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryTab threadId="t1" />);

    fireEvent.change(
      screen.getByPlaceholderText("workbench_memory_search_placeholder"),
      { target: { value: "godot" } },
    );
    await waitFor(() =>
      expect(screen.getByText("conteúdo")).toBeInTheDocument(),
    );

    fireEvent.click(
      screen.getByLabelText("workbench_memory_search_filter_fact"),
    );

    await waitFor(() => {
      const calls = fetchMock.mock.calls;
      expect(calls.some((c) => String(c[0]).includes("types=fact"))).toBe(true);
    });
  });
});

describe("MemoryTab — painel do Remember", () => {
  it("lista fatos e skills aprendidas vindas do endpoint", async () => {
    stubFetch({
      facts: [
        {
          key: "f1",
          content: "prefere respostas curtas",
          source: "",
          updated_at: "",
        },
      ],
      skills: [
        {
          id: "revisar-pr",
          name: "Revisar PR",
          description: "Como revisar",
          installed_at: "",
        },
      ],
    });

    render(<MemoryTab threadId="t1" />);

    await waitFor(() =>
      expect(screen.getByText("prefere respostas curtas")).toBeInTheDocument(),
    );
    expect(
      screen.getByText(
        'workbench_memory_journey_skill_label({"name":"Revisar PR"})',
      ),
    ).toBeInTheDocument();
  });

  it("mostra estado vazio quando nada foi aprendido, e também quando o endpoint falha (edge)", async () => {
    stubFetch({ facts: [], skills: [] });
    render(<MemoryTab threadId="t1" />);
    await waitFor(() =>
      expect(
        screen.getByText("workbench_memory_journey_empty"),
      ).toBeInTheDocument(),
    );

    // Erro/borda: endpoint fora do ar não pode derrubar a aba inteira nem
    // deixar o painel num limbo sem texto — degrada pro mesmo estado vazio.
    cleanup();
    stubFetch(null, false);
    render(<MemoryTab threadId="t1" />);
    await waitFor(() =>
      expect(
        screen.getByText("workbench_memory_journey_empty"),
      ).toBeInTheDocument(),
    );
  });
});
