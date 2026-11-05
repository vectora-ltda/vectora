// @vitest-environment jsdom
/**
 * useIngestPreview — debounce de 400ms, cancelamento de request obsoleta,
 * reset ao ficar sem workspaceId/path.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

const FETCH_MOCK = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", FETCH_MOCK);
  FETCH_MOCK.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mockOk(body: object): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

import { useIngestPreview } from "@/lib/hooks/use-ingest-preview";

describe("useIngestPreview", () => {
  it("busca contagem/lista 400ms após path mudar (happy) e devolve {total:0,files:[]} em erro (bad)", async () => {
    vi.useFakeTimers();
    try {
      FETCH_MOCK.mockResolvedValueOnce(
        mockOk({ total: 2, files: ["a.py", "b.py"] }),
      );
      const { result, rerender } = renderHook(
        ({ path }) => useIngestPreview("ws1", path, "all", "", ""),
        { initialProps: { path: "/proj" } },
      );

      expect(result.current.loading).toBe(true);
      expect(FETCH_MOCK).not.toHaveBeenCalled();

      await act(async () => {
        vi.advanceTimersByTime(400);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(FETCH_MOCK).toHaveBeenCalledTimes(1);
      expect(String(FETCH_MOCK.mock.calls[0][0])).toContain(
        "/workspaces/ws1/rag/ingest/preview",
      );
      expect(result.current.total).toBe(2);
      expect(result.current.files).toEqual(["a.py", "b.py"]);
      expect(result.current.loading).toBe(false);

      // Edge: resposta não-ok não mascara — expõe total:0/files:[] em vez
      // de propagar exceção ou manter o estado anterior silenciosamente.
      FETCH_MOCK.mockResolvedValueOnce(new Response("{}", { status: 500 }));
      rerender({ path: "/outra" });
      await act(async () => {
        vi.advanceTimersByTime(400);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(result.current.total).toBe(0);
      expect(result.current.files).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("debounce: só a última mudança em sequência rápida dispara fetch", async () => {
    vi.useFakeTimers();
    try {
      FETCH_MOCK.mockResolvedValue(mockOk({ total: 1, files: ["x"] }));
      const { rerender } = renderHook(
        ({ include }) => useIngestPreview("ws1", "/proj", "all", include, ""),
        { initialProps: { include: "a" } },
      );

      await act(async () => {
        vi.advanceTimersByTime(100);
      });
      rerender({ include: "ab" });
      await act(async () => {
        vi.advanceTimersByTime(100);
      });
      rerender({ include: "abc" });
      await act(async () => {
        vi.advanceTimersByTime(400);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(FETCH_MOCK).toHaveBeenCalledTimes(1);
      expect(String(FETCH_MOCK.mock.calls[0][0])).toContain("include_exts=abc");
    } finally {
      vi.useRealTimers();
    }
  });

  it("não dispara fetch sem workspaceId ou com path vazio", () => {
    const { result: noWs } = renderHook(() =>
      useIngestPreview(null, "/proj", "all", "", ""),
    );
    expect(FETCH_MOCK).not.toHaveBeenCalled();
    expect(noWs.current.loading).toBe(false);

    const { result: noPath } = renderHook(() =>
      useIngestPreview("ws1", "  ", "all", "", ""),
    );
    expect(FETCH_MOCK).not.toHaveBeenCalled();
    expect(noPath.current.loading).toBe(false);
  });
});
