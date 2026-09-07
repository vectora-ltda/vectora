// @vitest-environment jsdom
/**
 * Testes do cliente HTTP do painel Git (api.ts) — cobre o contrato de cada
 * função (URL/método/corpo) e a degradação em falha (res.ok=false, json
 * malformado) descrita nos comentários do próprio arquivo.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  fetchGitStatus,
  fetchBranches,
  apiCheckout,
  apiSync,
  apiMerge,
  apiCompare,
  apiCompareFile,
  fetchDiff,
  fetchDiffFile,
  apiGitFileAction,
  apiGitignoreAppend,
  apiGitCommit,
  fetchGitLog,
  fetchCommitDiff,
  apiRevert,
  apiStash,
  apiListConflicts,
  apiResolveConflict,
  fetchWorktrees,
  apiCreateWorktree,
  fetchPullRequests,
  apiCreatePR,
} from "../api";

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
  } as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("api — status/branches", () => {
  it("fetchGitStatus retorna o status quando res.ok, e null quando a resposta falha", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        is_git_repo: true,
        branch: "main",
        clean: true,
        ahead: 0,
        behind: 0,
      }),
    );
    const ok = await fetchGitStatus("ws1");
    expect(fetchMock).toHaveBeenCalledWith("/workspaces/ws1/git/status");
    expect(ok?.branch).toBe("main");

    fetchMock.mockResolvedValueOnce(jsonResponse({}, false, 500));
    const failed = await fetchGitStatus("ws1");
    expect(failed).toBeNull();
  });

  it("fetchBranches monta a URL com workspaceId codificado e retorna null em erro", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ current: "main", branches: ["main"], remotes: [] }),
    );
    const r = await fetchBranches("ws /1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws%20%2F1/git/branches",
    );
    expect(r?.current).toBe("main");

    fetchMock.mockResolvedValueOnce(jsonResponse({}, false, 404));
    expect(await fetchBranches("ws1")).toBeNull();
  });
});

describe("api — checkout/sync/merge", () => {
  it("apiCheckout envia ref e create no corpo", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ status: "ok", message: "" }),
    );
    const r = await apiCheckout("ws1", "feature", true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/git/checkout",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ ref: "feature", create: true }),
      }),
    );
    expect(r.status).toBe("ok");
  });

  it("apiCheckout usa create=false por padrão quando omitido", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ status: "ok", message: "" }),
    );
    await apiCheckout("ws1", "main");
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/git/checkout",
      expect.objectContaining({
        body: JSON.stringify({ ref: "main", create: false }),
      }),
    );
  });

  it("apiSync chama o endpoint da ação (fetch/pull/push)", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ status: "ok", message: "" }),
    );
    await apiSync("ws1", "pull");
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/git/pull",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("postJson devolve status=error quando o corpo não é JSON válido", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.reject(new Error("invalid json")),
    } as unknown as Response);
    const r = await apiSync("ws1", "fetch");
    expect(r).toEqual({ status: "error", message: "" });
  });

  it("apiMerge retorna conflicts em caso de conflito e status=error em payload malformado", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        status: "conflict",
        message: "conflitos",
        conflicts: ["a.ts"],
      }),
    );
    const r = await apiMerge("ws1", "feature");
    expect(r.status).toBe("conflict");
    expect(r.conflicts).toEqual(["a.ts"]);

    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.reject(new Error("bad")),
    } as unknown as Response);
    const errResult = await apiMerge("ws1", "feature");
    expect(errResult).toEqual({ status: "error", message: "", conflicts: [] });
  });
});

describe("api — compare", () => {
  it("apiCompare monta querystring com base/head e retorna null em falha", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        base: "main",
        head: "feature",
        ahead: 1,
        behind: 0,
        files: [],
        truncated: false,
      }),
    );
    const r = await apiCompare("ws1", "main", "feature");
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/git/compare?base=main&head=feature",
    );
    expect(r?.ahead).toBe(1);

    fetchMock.mockResolvedValueOnce(jsonResponse({}, false));
    expect(await apiCompare("ws1", "main", "feature")).toBeNull();
  });

  it("apiCompareFile retorna hunks vazios quando a resposta falha ou não traz hunks", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}, false));
    expect(await apiCompareFile("ws1", "main", "feature", "a.ts")).toEqual([]);

    fetchMock.mockResolvedValueOnce(jsonResponse({}));
    expect(await apiCompareFile("ws1", "main", "feature", "a.ts")).toEqual([]);
  });
});

describe("api — diff / commit", () => {
  it("fetchDiff e fetchDiffFile retornam null em falha", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}, false));
    expect(await fetchDiff("ws1")).toBeNull();

    fetchMock.mockResolvedValueOnce(jsonResponse({}, false));
    expect(await fetchDiffFile("ws1", "a.ts")).toBeNull();
  });

  it("fetchDiffFile retorna [] quando hunks está ausente no payload", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}));
    expect(await fetchDiffFile("ws1", "a.ts")).toEqual([]);
  });

  it("apiGitFileAction posta path para a ação (stage/unstage/discard)", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ status: "ok", message: "" }),
    );
    await apiGitFileAction("ws1", "stage", "a.ts");
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/git/stage",
      expect.objectContaining({ body: JSON.stringify({ path: "a.ts" }) }),
    );
  });

  it("apiGitignoreAppend envia caminho e modo de pasta", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ status: "ok", message: "" }),
    );
    await apiGitignoreAppend("ws1", "build/cache", true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/fs/gitignore/append",
      expect.objectContaining({
        body: JSON.stringify({ path: "build/cache", is_folder: true }),
      }),
    );
  });

  it("apiGitCommit envia mensagem e dry_run_hooks (default false)", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ status: "ok", message: "" }),
    );
    await apiGitCommit("ws1", "fix: bug");
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/git/commit",
      expect.objectContaining({
        body: JSON.stringify({
          message: "fix: bug",
          dry_run_hooks: false,
          body: null,
          amend: false,
        }),
      }),
    );
  });
});

describe("api — histórico", () => {
  it("fetchGitLog monta querystring com n=50 e offset", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ branch: "main", commits: [], has_more: false }),
    );
    await fetchGitLog("ws1", 50);
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/git/log?n=50&offset=50",
    );
  });

  it("fetchCommitDiff retorna string vazia em falha e o diff em sucesso", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}, false));
    expect(await fetchCommitDiff("ws1", "abc")).toBe("");

    fetchMock.mockResolvedValueOnce(jsonResponse({ diff: "@@ -1 +1 @@" }));
    expect(await fetchCommitDiff("ws1", "abc")).toBe("@@ -1 +1 @@");
  });

  it("apiRevert envia sha e no_commit=true", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ status: "ok", message: "" }),
    );
    await apiRevert("ws1", "abc123");
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/git/revert",
      expect.objectContaining({
        body: JSON.stringify({ sha: "abc123", no_commit: true }),
      }),
    );
  });
});

describe("api — stash/conflitos/worktrees/PR", () => {
  it("apiStash retorna entries/message vazios em falha", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}, false));
    const r = await apiStash("ws1", "list");
    expect(r).toEqual({ entries: [], message: "" });
  });

  it("apiStash envia action e opts no corpo, e retorna entries em sucesso", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ entries: [{ index: 0, label: "wip" }], message: "ok" }),
    );
    const r = await apiStash("ws1", "push", { name: "wip" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/git/stash",
      expect.objectContaining({
        body: JSON.stringify({ action: "push", name: "wip" }),
      }),
    );
    expect(r.entries).toEqual([{ index: 0, label: "wip" }]);
  });

  it("apiListConflicts extrai path de cada arquivo e retorna [] em falha", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ files: [{ path: "a.ts" }, { path: "b.ts" }] }),
    );
    expect(await apiListConflicts("ws1")).toEqual(["a.ts", "b.ts"]);

    fetchMock.mockResolvedValueOnce(jsonResponse({}, false));
    expect(await apiListConflicts("ws1")).toEqual([]);
  });

  it("apiResolveConflict envia path e resolution", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ status: "ok", message: "" }),
    );
    await apiResolveConflict("ws1", "a.ts", "theirs");
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/git/resolve-conflict",
      expect.objectContaining({
        body: JSON.stringify({ path: "a.ts", resolution: "theirs" }),
      }),
    );
  });

  it("fetchWorktrees retorna [] em falha e a lista em sucesso", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}, false));
    expect(await fetchWorktrees("ws1")).toEqual([]);

    fetchMock.mockResolvedValueOnce(
      jsonResponse({ worktrees: [{ path: "/tmp/wt1" }] }),
    );
    expect(await fetchWorktrees("ws1")).toEqual([{ path: "/tmp/wt1" }]);
  });

  it("apiCreateWorktree retorna res.ok e envia workspace_id/name/branch", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}, true));
    const ok = await apiCreateWorktree("ws1", "feat", "main");
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/worktrees",
      expect.objectContaining({
        body: JSON.stringify({
          workspace_id: "ws1",
          name: "feat",
          branch: "main",
        }),
      }),
    );
    expect(ok).toBe(true);

    fetchMock.mockResolvedValueOnce(jsonResponse({}, false));
    expect(await apiCreateWorktree("ws1", "feat")).toBe(false);
  });

  it("fetchPullRequests retorna disponibilidade/lista padrão em falha", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}, false));
    expect(await fetchPullRequests("ws1")).toEqual({
      available: false,
      prs: [],
    });

    fetchMock.mockResolvedValueOnce(
      jsonResponse({ available: true, prs: [{ number: 1 }] }),
    );
    expect(await fetchPullRequests("ws1")).toEqual({
      available: true,
      prs: [{ number: 1 }],
    });
  });

  it("apiCreatePR posta title/body/base", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ status: "ok", message: "" }),
    );
    await apiCreatePR("ws1", "Meu PR", "descrição", "main");
    expect(fetchMock).toHaveBeenCalledWith(
      "/workspaces/ws1/pr",
      expect.objectContaining({
        body: JSON.stringify({
          title: "Meu PR",
          body: "descrição",
          base: "main",
        }),
      }),
    );
  });
});
