/**
 * Tests para fetchFile/apiUpdateFile: leitura, escrita com conflito otimista
 * (HTTP 412) e erros.
 */

import { describe, expect, it, afterEach, vi } from "vitest";
import { fetchFile, apiUpdateFile } from "@/lib/api/fs-files";

afterEach(() => vi.restoreAllMocks());

function res(init: {
  ok?: boolean;
  status?: number;
  json?: unknown;
  jsonThrows?: boolean;
}): Response {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => {
      if (init.jsonThrows) throw new Error("no body");
      return init.json;
    },
  } as unknown as Response;
}

describe("fetchFile", () => {
  it("devolve o conteúdo quando ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => res({ json: { content: "abc", sha256: "h" } })),
    );
    const out = await fetchFile("ws", "a.ts");
    expect(out).toEqual({ content: "abc", sha256: "h" });
  });

  it("devolve null quando a resposta não é ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => res({ ok: false, status: 404 })),
    );
    expect(await fetchFile("ws", "x")).toBeNull();
  });
});

describe("apiUpdateFile", () => {
  it("HTTP 412 vira conflito otimista", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => res({ ok: false, status: 412 })),
    );
    expect(await apiUpdateFile("ws", "a", "c", "sha")).toEqual({
      ok: false,
      conflict: true,
    });
  });

  it("sucesso devolve ok + sha256", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => res({ json: { status: "ok", sha256: "new" } })),
    );
    expect(await apiUpdateFile("ws", "a", "c", "old")).toEqual({
      ok: true,
      sha256: "new",
    });
  });

  it("status != ok devolve erro com a mensagem", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => res({ ok: true, json: { message: "deu ruim" } })),
    );
    expect(await apiUpdateFile("ws", "a", "c", null)).toEqual({
      ok: false,
      conflict: false,
      message: "deu ruim",
    });
  });

  it("resposta sem corpo JSON não quebra (erro)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => res({ ok: false, status: 500, jsonThrows: true })),
    );
    const out = await apiUpdateFile("ws", "a", "c", null);
    expect(out.ok).toBe(false);
  });
});
