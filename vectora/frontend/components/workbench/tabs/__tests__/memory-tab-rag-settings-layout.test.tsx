// @vitest-environment jsdom
/**
 * Regressão de layout: as configurações devem abrir pelo host modal
 * responsivo, sem transformar a linha de busca em uma coluna extra.
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

vi.mock("@/lib/hooks/chat/use-thread-messages", () => ({
  useThreadMessages: () => [[], vi.fn()],
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

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (String(url).includes("/rag/settings"))
        return new Response(
          JSON.stringify({
            reranker_enabled: true,
            reranker_top_k: 5,
            rerank_provider: "auto",
            embed_provider: "auto",
            ingest_file_types: [],
          }),
        );
      if (String(url).includes("/rag/collections"))
        return new Response(JSON.stringify({ collections: [] }));
      return new Response(JSON.stringify({}));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("MemoryTab — layout do painel de configurações do RAG", () => {
  it("botão de configurar e busca dividem a mesma linha", async () => {
    render(<MemoryTab threadId="t1" />);

    const btn = await screen.findByTestId("rag-settings-btn");
    const input = screen.getByPlaceholderText(
      "workbench_memory_search_placeholder",
    );

    expect(btn.parentElement).toBe(input.parentElement?.parentElement);
  });

  it("painel aberto usa o host modal responsivo fora da linha de busca", async () => {
    render(<MemoryTab threadId="t1" />);

    const btn = await screen.findByTestId("rag-settings-btn");
    const row = btn.parentElement as HTMLElement;

    fireEvent.click(btn);

    const panel = await waitFor(() => screen.getByTestId("rag-settings-panel"));
    expect(row.contains(panel)).toBe(false);
    expect(panel).toHaveAttribute("role", "dialog");
    expect(panel).toHaveClass("overflow-hidden");
  });
});
