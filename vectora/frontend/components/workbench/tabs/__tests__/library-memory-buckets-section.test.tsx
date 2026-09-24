// @vitest-environment jsdom
/**
 * MemoryBucketsSection — seção Memory Buckets da Library.
 *
 * Cobre: lista os buckets do catálogo; instalar chama POST /memory-buckets/install
 * e vira "Installed"; erro/borda: bucket com embed_model diferente do atual
 * mostra aviso de incompatibilidade sem bloquear o botão; falha de instalação
 * (status "error") mostra a mensagem sem quebrar a lista; catálogo vazio
 * mostra o estado vazio específico.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  waitFor,
  fireEvent,
  act,
} from "@testing-library/react";

import { MemoryBucketsSection } from "../library-memory-buckets-section";
import { useLibraryStore } from "@/lib/stores/library-store";

afterEach(cleanup);

beforeEach(() => {
  useLibraryStore.setState({
    memoryItems: [],
    memoryLoading: false,
    memoryFetchedAt: null,
    memoryQuery: "",
    memoryError: null,
  });
});

const CATALOG = [
  {
    id: "b1",
    name: "Bucket 1",
    description: "Documentação interna vetorizada",
    embed_model: "embed-multilingual-v3.0",
    verified: true,
    publisher: "Vectora Team",
    downloads_count: 42,
    license: "MIT",
  },
  {
    id: "b2",
    name: "Bucket 2",
    description: "Outro bucket comunitário",
    embed_model: "voyage-3",
    verified: false,
    downloads_count: 3,
    license: "MIT",
  },
];

function mockFetch({
  catalog = CATALOG as typeof CATALOG,
  installStatus = "installed" as string,
} = {}) {
  global.fetch = vi
    .fn()
    .mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/memory-buckets/catalog") {
        return Promise.resolve({
          ok: true,
          json: async () => catalog,
        } as Response);
      }
      if (url === "/memory-buckets/install" && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: async () =>
            installStatus === "error"
              ? { status: "error", error: "falha ao instalar" }
              : { status: installStatus, collection: "shared_b1" },
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });
}

describe("MemoryBucketsSection", () => {
  beforeEach(() => {
    mockFetch();
  });

  it("lista os buckets do catálogo", async () => {
    render(<MemoryBucketsSection query="" />);
    await waitFor(() => {
      expect(screen.getByText("Bucket 1")).toBeTruthy();
      expect(screen.getByText("Bucket 2")).toBeTruthy();
    });
    expect(screen.getByText(/Vectora Team/)).toBeTruthy();
  });

  it("instalar um bucket chama POST /memory-buckets/install e vira Installed", async () => {
    render(<MemoryBucketsSection query="" />);
    await waitFor(() => expect(screen.getByText("Bucket 1")).toBeTruthy());

    const card = screen.getByText("Bucket 1").closest("div.rounded-lg")!;
    fireEvent.click(
      Array.from(card.querySelectorAll("button")).find((b) =>
        b.textContent?.includes("Install"),
      )!,
    );

    await waitFor(() => {
      const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls;
      expect(
        calls.some(
          (c) => c[0] === "/memory-buckets/install" && c[1]?.method === "POST",
        ),
      ).toBe(true);
      expect(screen.getByText("Installed")).toBeTruthy();
    });
  });

  it("erro/borda: bucket com embed_model incompatível mostra aviso sem bloquear instalação", async () => {
    render(
      <MemoryBucketsSection
        query=""

        currentEmbedModel="embed-multilingual-v3.0"
      />,
    );
    await waitFor(() => expect(screen.getByText("Bucket 2")).toBeTruthy());

    expect(screen.getAllByText(/voyage-3/).length).toBeGreaterThan(0);
    const card = screen.getByText("Bucket 2").closest("div.rounded-lg")!;
    const installButton = Array.from(card.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("Install"),
    )!;
    expect(installButton.hasAttribute("disabled")).toBe(false);
  });

  it("erro/borda: falha de instalação (status 'error') mostra mensagem sem quebrar a lista", async () => {
    mockFetch({ installStatus: "error" });
    render(<MemoryBucketsSection query="" />);
    await waitFor(() => expect(screen.getByText("Bucket 1")).toBeTruthy());

    const card = screen.getByText("Bucket 1").closest("div.rounded-lg")!;
    fireEvent.click(
      Array.from(card.querySelectorAll("button")).find((b) =>
        b.textContent?.includes("Install"),
      )!,
    );

    await waitFor(() => {
      expect(screen.getByText("falha ao instalar")).toBeTruthy();
    });
    expect(screen.getByText("Bucket 2")).toBeTruthy();
  });

  it("digitar na busca dispara /memory-buckets/catalog?q= com debounce de 350ms; erro de rede na busca seguinte mantém o último resultado bom e mostra erro discreto", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/license/status") {
        return {
          ok: true,
          json: async () => ({ configured: false }),
        } as Response;
      }
      if (url === "/memory-buckets/catalog") {
        return { ok: true, json: async () => CATALOG } as Response;
      }
      if (url === "/memory-buckets/catalog?q=bucket+1") {
        return { ok: true, json: async () => [CATALOG[0]] } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    const { rerender } = render(<MemoryBucketsSection query="" />);
    await vi.waitFor(() => expect(screen.getByText("Bucket 1")).toBeTruthy());

    rerender(<MemoryBucketsSection query="bucket 1" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });
    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          (c) => c[0] === "/memory-buckets/catalog?q=bucket+1",
        ),
      ).toBe(true);
      expect(screen.queryByText("Bucket 2")).toBeNull();
    });

    fetchMock.mockImplementation(async (url: string) => {
      if (url === "/memory-buckets/catalog?q=falha") throw new Error("offline");
      return { ok: true, json: async () => ({}) } as Response;
    });
    rerender(<MemoryBucketsSection query="falha" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });

    await vi.waitFor(() => {
      expect(screen.getByText("Bucket 1")).toBeTruthy();
      expect(screen.getByText("Error searching memory buckets")).toBeTruthy();
    });

    vi.useRealTimers();
  });

  it("erro/borda: catálogo vazio mostra o estado vazio específico", async () => {
    mockFetch({ catalog: [] });
    render(<MemoryBucketsSection query="" />);
    await waitFor(() => {
      expect(screen.getByText(/no memory buckets/i)).toBeTruthy();
    });
  });
});
