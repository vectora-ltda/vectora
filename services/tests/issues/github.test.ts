import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import { listComments } from "../../src/issues/github";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GitHub comments pagination", () => {
  it("rejeita payload de comentários que não seja uma lista", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ items: [] }), { status: 200 }),
      ),
    );

    await expect(
      listComments(
        { ...env, GITHUB_TOKEN: "test-token" },
        "vectora-ltda/vectora-issues",
        42,
      ),
    ).rejects.toMatchObject({
      status: 502,
      message: "github_comments_invalid",
    });
  });

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
