import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import { findIssueByMarker, listComments } from "../../src/issues/github";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GitHub comments pagination", () => {
  it("alcança a página 11 e encontra comentários além de mil itens", async () => {
    const pages: number[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const page = Number(new URL(String(input)).searchParams.get("page"));
        pages.push(page);
        const comments =
          page < 11
            ? Array.from({ length: 100 }, (_, index) => ({
                id: page * 100 + index,
                body: `comentário ${page}-${index}`,
                html_url: `https://github.com/comment/${page}-${index}`,
                created_at: new Date().toISOString(),
              }))
            : [
                {
                  id: 1101,
                  body: "<!-- marcador-pagina-11 -->",
                  html_url: "https://github.com/comment/1101",
                  created_at: new Date().toISOString(),
                },
              ];
        return new Response(JSON.stringify(comments), { status: 200 });
      }),
    );

    const comments = await listComments(
      { ...env, GITHUB_TOKEN: "test-token" },
      "vectora-ltda/vectora-issues",
      42,
    );

    expect(pages).toEqual(Array.from({ length: 11 }, (_, index) => index + 1));
    expect(comments.at(-1)?.body).toContain("pagina-11");
  });
});

describe("GitHub issue marker pagination", () => {
  it("alcança o candidato confiável em uma página posterior", async () => {
    const pages: number[] = [];
    const queries: string[] = [];
    const forged = Array.from({ length: 100 }, (_, index) => ({
      number: index + 1,
      title: "forjada",
      body: "<!-- vectora-company-issue:issue-1 -->",
      state: "open" as const,
      html_url: `https://github.com/forged/${index + 1}`,
      user: { login: "attacker" },
    }));
    const trusted = {
      number: 101,
      title: "confiável",
      body: "<!-- vectora-company-issue:issue-1 -->",
      state: "open" as const,
      html_url: "https://github.com/vectora/101",
      user: { login: "vectora-bot" },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const page = Number(new URL(String(input)).searchParams.get("page"));
        pages.push(page);
        queries.push(new URL(String(input)).searchParams.get("q") ?? "");
        return new Response(
          JSON.stringify({ items: page === 1 ? forged : [trusted] }),
          { status: 200 },
        );
      }),
    );

    const candidates = await findIssueByMarker(
      { ...env, GITHUB_TOKEN: "test-token" },
      "vectora-ltda/vectora-issues",
      "vectora-company-issue:issue-1",
      "vectora-bot",
    );

    expect(pages).toEqual([1, 2]);
    expect(queries[0]).toContain("author:vectora-bot");
    expect(candidates).toHaveLength(101);
    expect(candidates.at(-1)?.user?.login).toBe("vectora-bot");
  });

  it("retries a transient network failure with a fresh abort signal", async () => {
    let attempts = 0;
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        expect(String(input)).toContain("search/issues");
        expect(init?.signal).toBeInstanceOf(AbortSignal);
        signals.push(init?.signal as AbortSignal);
        attempts += 1;
        if (attempts === 1) throw new TypeError("temporary network failure");
        return new Response(JSON.stringify({ items: [] }), { status: 200 });
      }),
    );

    await expect(
      findIssueByMarker(
        { ...env, GITHUB_TOKEN: "test-token" },
        "vectora-ltda/vectora-issues",
        "vectora-company-issue:retry",
        "vectora-bot",
      ),
    ).resolves.toEqual([]);
    expect(attempts).toBe(2);
    expect(signals[0]).not.toBe(signals[1]);
  });

  it("rejeita repositório ou marcador vazios antes da busca", async () => {
    const request = vi.fn();
    vi.stubGlobal("fetch", request);
    const githubEnv = { ...env, GITHUB_TOKEN: "test-token" };

    await expect(
      findIssueByMarker(githubEnv, "", "marker"),
    ).rejects.toMatchObject({ status: 400, message: "github_repo_invalid" });
    await expect(
      findIssueByMarker(githubEnv, "vectora-ltda/vectora", ""),
    ).rejects.toMatchObject({ status: 400, message: "github_marker_invalid" });
    expect(request).not.toHaveBeenCalled();
  });
});
