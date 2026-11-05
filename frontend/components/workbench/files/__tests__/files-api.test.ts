// @vitest-environment jsdom
/**
 * files-api — clients HTTP do Files workbench: URL/método corretos,
 * parsing de resposta e comportamento em erro (status não-ok, JSON malformado).
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";

import {
  fetchTree,
  fetchDiffSummary,
  apiFsCreate,
  apiFsCreateFile,
  apiFsDelete,
  apiFsMove,
  apiFsSearch,
  apiFsGitLogFile,
  apiFsGitShow,
} from "../files-api";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("fetchTree", () => {
  it("chama /tree com querystring de path e retorna as entradas", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ entries: [{ name: "a" }] }));
    const result = await fetchTree("ws1", "src");
    expect(fetchMock).toHaveBeenCalledWith("/workspaces/ws1/tree?path=src");
    expect(result).toEqual([{ name: "a" }]);
  });

  it("resposta sem campo entries retorna array vazio; status não-ok retorna null", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}));
    expect(await fetchTree("ws1", "")).toEqual([]);

    fetchMock.mockResolvedValue(jsonResponse({}, 500));
    expect(await fetchTree("ws1", "")).toBeNull();
  });
});

describe("fetchDiffSummary", () => {
  it("chama /git/diff e retorna o corpo JSON", async () => {
    const summary = { files: [], total_additions: 0, total_deletions: 0 };
    fetchMock.mockResolvedValue(jsonResponse(summary));
    const result = await fetchDiffSummary("ws1");
    expect(fetchMock).toHaveBeenCalledWith("/workspaces/ws1/git/diff");
    expect(result).toEqual(summary);
  });

  it("status não-ok retorna null", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 404));
    expect(await fetchDiffSummary("ws1")).toBeNull();
  });
});

describe("apiFsCreate", () => {
  it("faz POST em /fs/{type} com o path no corpo e retorna res.ok", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 201));
    const ok = await apiFsCreate("ws1", "file", "a/b.ts");
    expect(fetchMock).toHaveBeenCalledWith("/workspaces/ws1/fs/file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: "a/b.ts" }),
    });
    expect(ok).toBe(true);
  });

  it("status não-ok (ex: duplicado) retorna false", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 409));
    expect(await apiFsCreate("ws1", "dir", "a")).toBe(false);
  });
});

describe("apiFsCreateFile", () => {
  it("faz POST em /fs/file com path e content", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: "ok" }, 200));
    const result = await apiFsCreateFile("ws1", "vectora.toml", "[sandbox]\n");
    expect(fetchMock).toHaveBeenCalledWith("/workspaces/ws1/fs/file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: "vectora.toml", content: "[sandbox]\n" }),
    });
    expect(result.ok).toBe(true);
  });
});

describe("apiFsDelete", () => {
  it("faz DELETE em /fs?path= sem permanent por default", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 200));
    await apiFsDelete("ws1", "a/b.ts");
    expect(fetchMock).toHaveBeenCalledWith("/workspaces/ws1/fs?path=a%2Fb.ts", {
      method: "DELETE",
    });
  });

  it("permanent=true adiciona querystring permanent=true", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 200));
    await apiFsDelete("ws1", "a/b.ts", true);
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("permanent=true");
  });
});

describe("apiFsMove", () => {
  it("faz POST em /fs/move com from_path/to_path e retorna ok+message", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ message: "ok" }, 200));
    const result = await apiFsMove("ws1", "a.ts", "b.ts");
    expect(fetchMock).toHaveBeenCalledWith("/workspaces/ws1/fs/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_path: "a.ts", to_path: "b.ts" }),
    });
    expect(result).toEqual({ ok: true, message: "ok" });
  });

  it("JSON de erro malformado não quebra o parsing (cai no catch -> {})", async () => {
    fetchMock.mockResolvedValue(new Response("not json", { status: 500 }));
    const result = await apiFsMove("ws1", "a.ts", "b.ts");
    expect(result.ok).toBe(false);
    expect(result.message).toBeUndefined();
  });
});

describe("apiFsSearch", () => {
  it("chama /fs/search com querystring q e retorna hits", async () => {
    const hits = {
      hits: [{ path: "a.ts", line_number: 1, line_text: "x" }],
      truncated: false,
    };
    fetchMock.mockResolvedValue(jsonResponse(hits));
    const result = await apiFsSearch("ws1", "foo");
    expect(fetchMock).toHaveBeenCalledWith("/workspaces/ws1/fs/search?q=foo");
    expect(result).toEqual(hits);
  });

  it("path opcional adiciona querystring path; status não-ok retorna null", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ hits: [], truncated: false }));
    await apiFsSearch("ws1", "foo", "src");
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("path=src");

    fetchMock.mockResolvedValue(jsonResponse({}, 500));
    expect(await apiFsSearch("ws1", "foo")).toBeNull();
  });
});

describe("apiFsGitLogFile", () => {
  it("chama /git/log/file com path e n (default 50)", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ path: "a.ts", entries: [] }));
    await apiFsGitLogFile("ws1", "a.ts");
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/git/log/file?path=a.ts&n=50",
    );
  });

  it("n customizado é respeitado; status não-ok retorna null", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ path: "a.ts", entries: [] }));
    await apiFsGitLogFile("ws1", "a.ts", 5);
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("n=5");

    fetchMock.mockResolvedValue(jsonResponse({}, 404));
    expect(await apiFsGitLogFile("ws1", "a.ts")).toBeNull();
  });
});

describe("apiFsGitShow", () => {
  it("chama /git/show com sha e path, retorna conteúdo", async () => {
    const body = {
      path: "a.ts",
      sha: "abc",
      content: "x",
      binary: false,
      truncated: false,
    };
    fetchMock.mockResolvedValue(jsonResponse(body));
    const result = await apiFsGitShow("ws1", "abc", "a.ts");
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/git/show?sha=abc&path=a.ts",
    );
    expect(result).toEqual(body);
  });

  it("status não-ok (sha inexistente) retorna null", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 404));
    expect(await apiFsGitShow("ws1", "deadbeef", "a.ts")).toBeNull();
  });
});
