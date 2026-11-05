// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  act,
  waitFor,
} from "@testing-library/react";

// ── fetch mock ───────────────────────────────────────────────────────────────

const MEMORIES: {
  key: string;
  content: string;
  metadata: object;
  updated_at: string;
}[] = [];

const FETCH_MOCK = vi.fn(async (url: string, opts?: RequestInit) => {
  const method = opts?.method ?? "GET";
  const urlStr = String(url);

  if (method === "GET" && urlStr.match(/\/memory(\?|$)/)) {
    return new Response(
      JSON.stringify({ memories: [...MEMORIES], total: MEMORIES.length }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }
  if (method === "POST" && urlStr.match(/\/memory(\?|$)/)) {
    const body = JSON.parse(String(opts?.body ?? "{}"));
    const item = {
      key: body.key || `mem_${Date.now()}`,
      content: body.content,
      metadata: {},
      updated_at: new Date().toISOString(),
    };
    MEMORIES.push(item);
    return new Response(JSON.stringify({ status: "created", key: item.key }), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    });
  }
  if (method === "PUT" && urlStr.match(/\/memory\/[^/]+$/)) {
    return new Response(JSON.stringify({ status: "updated" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  if (method === "DELETE" && urlStr.match(/\/memory\/[^/?]+$/)) {
    const key = urlStr.split("/memory/")[1];
    const idx = MEMORIES.findIndex((m) => m.key === key);
    if (idx >= 0) MEMORIES.splice(idx, 1);
    return new Response(JSON.stringify({ status: "deleted" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  if (method === "DELETE" && urlStr.match(/\/memory(\?|$)/)) {
    MEMORIES.length = 0;
    return new Response(JSON.stringify({ status: "deleted", deleted: 0 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  return new Response(JSON.stringify({}), { status: 200 });
});

beforeEach(() => {
  FETCH_MOCK.mockClear();
  vi.stubGlobal("fetch", FETCH_MOCK);
  MEMORIES.length = 0;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ── mocks ────────────────────────────────────────────────────────────────────

vi.mock("@/lib/stores/toast-store", () => ({
  useToastStore: { getState: () => ({ success: vi.fn(), error: vi.fn() }) },
}));

vi.mock("@/lib/paraglide/messages", () => ({
  m: new Proxy(
    {},
    {
      get:
        (_t, prop) =>
        (..._args: unknown[]) =>
          String(prop),
    },
  ),
}));

vi.mock("@/lib/i18n-dyn", () => ({
  mDyn: (_key: string, params: { n: number }) => `${params.n} memória(s)`,
}));

// ── helpers ───────────────────────────────────────────────────────────────────

async function renderMemoriaTab() {
  const { MemoriaTab } = await import("../memoria-tab");
  return render(<MemoriaTab />);
}

// ── testes ────────────────────────────────────────────────────────────────────

describe("MemoriaTab", () => {
  it("exibe empty state quando não há memórias", async () => {
    await act(async () => {
      await renderMemoriaTab();
    });
    await waitFor(() => {
      expect(screen.queryByText("memory_empty_title")).toBeTruthy();
    });
  });

  it("lista memórias carregadas do servidor", async () => {
    MEMORIES.push({
      key: "idioma",
      content: "Prefere português",
      metadata: {},
      updated_at: "2024-01-01T00:00:00Z",
    });
    await act(async () => {
      await renderMemoriaTab();
    });
    await waitFor(() => {
      expect(screen.getByText("idioma")).toBeTruthy();
      expect(screen.getByText("Prefere português")).toBeTruthy();
    });
  });

  it("erro: exibe mensagem quando o servidor retorna 500", async () => {
    FETCH_MOCK.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "erro" }), { status: 500 }),
    );
    await act(async () => {
      await renderMemoriaTab();
    });
    await waitFor(() => {
      expect(screen.getByText("memory_error_load")).toBeTruthy();
    });
  });

  it("cria nova memória: POST /memory chamado com conteúdo correto", async () => {
    await act(async () => {
      await renderMemoriaTab();
    });
    await waitFor(() => screen.getByText("memory_add"));

    await act(async () => {
      fireEvent.click(screen.getByText("memory_add"));
    });

    await waitFor(() => screen.getByText("memory_add_title"));

    const textarea = screen.getByPlaceholderText(
      "memory_add_content_placeholder",
    );
    await act(async () => {
      fireEvent.change(textarea, {
        target: { value: "nova memória de teste" },
      });
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "memory_save" }));
    });

    await waitFor(() => {
      const calls = FETCH_MOCK.mock.calls;
      const postCall = calls.find(
        ([url, o]) =>
          o?.method === "POST" && String(url).match(/\/memory(\?|$)/),
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(String(postCall![1]!.body));
      expect(body.content).toBe("nova memória de teste");
    });
  });

  it("deleta memória: DELETE /memory/:key chamado e item some da lista", async () => {
    MEMORIES.push({
      key: "del_test",
      content: "Conteúdo a deletar",
      metadata: {},
      updated_at: "2024-01-01T00:00:00Z",
    });
    await act(async () => {
      await renderMemoriaTab();
    });
    await waitFor(() => screen.getByText("Conteúdo a deletar"));

    const deleteButtons = screen.getAllByText("memory_delete");
    await act(async () => {
      fireEvent.click(deleteButtons[0]);
    });

    await waitFor(() => {
      const calls = FETCH_MOCK.mock.calls;
      const deleteCall = calls.find(
        ([url, o]) =>
          o?.method === "DELETE" && String(url).includes("del_test"),
      );
      expect(deleteCall).toBeTruthy();
    });
  });

  it("limpar tudo: dialog de confirmação exibido e DELETE /memory chamado", async () => {
    MEMORIES.push(
      {
        key: "k1",
        content: "m1",
        metadata: {},
        updated_at: "2024-01-01T00:00:00Z",
      },
      {
        key: "k2",
        content: "m2",
        metadata: {},
        updated_at: "2024-01-01T00:00:00Z",
      },
    );
    await act(async () => {
      await renderMemoriaTab();
    });
    await waitFor(() => screen.getByText("m1"));

    await act(async () => {
      fireEvent.click(screen.getByText("memory_clear_all"));
    });

    await waitFor(() => {
      expect(screen.getByText("memory_clear_title")).toBeTruthy();
    });

    const confirmButtons = screen.getAllByText("memory_clear_all");
    const confirmBtn = confirmButtons[confirmButtons.length - 1];
    await act(async () => {
      fireEvent.click(confirmBtn);
    });

    await waitFor(() => {
      const calls = FETCH_MOCK.mock.calls;
      const clearCall = calls.find(
        ([url, o]) =>
          o?.method === "DELETE" && String(url).match(/\/memory(\?|$)/),
      );
      expect(clearCall).toBeTruthy();
    });
  });
});
