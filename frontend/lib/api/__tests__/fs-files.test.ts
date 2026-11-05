// @vitest-environment jsdom
/**
 * Tests para lib/api/fs-files: fetchFile (leitura) e apiUpdateFile (escrita
 * com conflito otimista HTTP 412).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fetchFile, apiUpdateFile } from "@/lib/api/fs-files";

function res(
  body: unknown,
  { ok = true, status = 200 }: { ok?: boolean; status?: number } = {},
): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

function resNoBody(status: number): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      throw new Error("no body");
    },
  } as unknown as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("fetchFile", () => {
  it("devolve o JSON quando ok", async () => {
    fetchMock.mockResolvedValueOnce(res({ content: "olá", sha256: "abc" }));
    const out = await fetchFile("ws1", "a.ts");
    expect(out).toEqual({ content: "olá", sha256: "abc" });
  });

  it("devolve null em 404", async () => {
    fetchMock.mockResolvedValueOnce(res(null, { ok: false, status: 404 }));
    expect(await fetchFile("ws1", "a.ts")).toBeNull();
  });

  it("devolve null em 500", async () => {
    fetchMock.mockResolvedValueOnce(res(null, { ok: false, status: 500 }));
    expect(await fetchFile("ws1", "a.ts")).toBeNull();
  });

  it("monta a URL com workspaceId e path codificados", async () => {
    fetchMock.mockResolvedValueOnce(res({ content: "" }));
    await fetchFile("ws 1", "src/a b.ts");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/workspaces/ws%201/file");
    expect(url).toContain("path=src%2Fa+b.ts");
  });

  it("usa GET (sem method)", async () => {
    fetchMock.mockResolvedValueOnce(res({ content: "" }));
    await fetchFile("ws1", "a.ts");
    const opts = fetchMock.mock.calls[0][1];
    expect(opts).toBeUndefined();
  });
});

describe("apiUpdateFile", () => {
  it("412 vira conflict true", async () => {
    fetchMock.mockResolvedValueOnce(res(null, { ok: false, status: 412 }));
    const out = await apiUpdateFile("ws1", "a.ts", "novo", "sha");
    expect(out).toEqual({ ok: false, conflict: true });
  });

  it("status ok devolve ok:true com sha256", async () => {
    fetchMock.mockResolvedValueOnce(res({ status: "ok", sha256: "newsha" }));
    const out = await apiUpdateFile("ws1", "a.ts", "x", "old");
    expect(out).toEqual({ ok: true, sha256: "newsha" });
  });

  it("status ok sem sha256 devolve sha256 null", async () => {
    fetchMock.mockResolvedValueOnce(res({ status: "ok" }));
    const out = await apiUpdateFile("ws1", "a.ts", "x", null);
    expect(out).toEqual({ ok: true, sha256: null });
  });

  it("ok HTTP mas status != ok devolve falha com message", async () => {
    fetchMock.mockResolvedValueOnce(
      res({ status: "error", message: "disco cheio" }),
    );
    const out = await apiUpdateFile("ws1", "a.ts", "x", null);
    expect(out).toEqual({
      ok: false,
      conflict: false,
      message: "disco cheio",
    });
  });

  it("HTTP 500 devolve falha sem conflito", async () => {
    fetchMock.mockResolvedValueOnce(
      res({ message: "erro" }, { ok: false, status: 500 }),
    );
    const out = await apiUpdateFile("ws1", "a.ts", "x", null);
    expect(out).toMatchObject({ ok: false, conflict: false });
  });

  it("resposta sem corpo JSON e não-ok vira falha", async () => {
    fetchMock.mockResolvedValueOnce(resNoBody(500));
    const out = await apiUpdateFile("ws1", "a.ts", "x", null);
    expect(out).toMatchObject({ ok: false, conflict: false });
  });

  it("resposta ok sem corpo JSON (status ausente) vira falha", async () => {
    fetchMock.mockResolvedValueOnce(resNoBody(200));
    const out = await apiUpdateFile("ws1", "a.ts", "x", null);
    expect(out).toMatchObject({ ok: false, conflict: false });
  });

  it("envia PUT com content e expected_sha256 no body", async () => {
    fetchMock.mockResolvedValueOnce(res({ status: "ok", sha256: "s" }));
    await apiUpdateFile("ws1", "a.ts", "conteúdo", "sha-antiga");
    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/workspaces/ws1/fs/file");
    expect(opts.method).toBe("PUT");
    const body = JSON.parse(opts.body as string);
    expect(body.content).toBe("conteúdo");
    expect(body.expected_sha256).toBe("sha-antiga");
  });

  it("expected_sha256 null é enviado como null", async () => {
    fetchMock.mockResolvedValueOnce(res({ status: "ok", sha256: "s" }));
    await apiUpdateFile("ws1", "a.ts", "x", null);
    const opts = fetchMock.mock.calls[0][1] as RequestInit;
    const body = JSON.parse(opts.body as string);
    expect(body.expected_sha256).toBeNull();
  });

  it("define Content-Type application/json", async () => {
    fetchMock.mockResolvedValueOnce(res({ status: "ok", sha256: "s" }));
    await apiUpdateFile("ws1", "a.ts", "x", null);
    const opts = fetchMock.mock.calls[0][1] as RequestInit;
    expect((opts.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
  });
});
