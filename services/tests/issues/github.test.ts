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
    );

    expect(pages).toEqual([1, 2]);
    expect(candidates).toHaveLength(101);
    expect(candidates.at(-1)?.user?.login).toBe("vectora-bot");
  });
});
