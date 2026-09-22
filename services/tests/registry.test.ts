import {
  createExecutionContext,
  env,
  waitOnExecutionContext,
} from "cloudflare:test";
import { describe, expect, it } from "vitest";
import worker from "../src/index";

describe("registry — catálogos reais de MCP/Skills (D1)", () => {
  it("GET /registry/mcp expõe somente entradas sincronizadas do registry oficial", async () => {
    await env.DB.prepare(
      `INSERT INTO mcp_catalog
        (id, name, description, install_cmd, category, catalog_source, stars_count, downloads_count)
       VALUES
        ('official-low', 'Official Low', 'd', 'npx low', 'community', 'official', 10, 2),
        ('official-high', 'Official High', 'd', 'npx high', 'community', 'official', 20, 1),
        ('legacy-ytdl', 'Legacy YTDL', 'd', 'npx legacy', 'community', 'github', 999, 999)`,
    ).run();
    const ctx = createExecutionContext();
    const req = new Request("https://services.vectora.company/registry/mcp");
    const res = await worker.fetch(req, env, ctx);
    await waitOnExecutionContext(ctx);

    expect(res.status).toBe(200);
    const body = await res.json<{
      entries: Array<{ id: string; catalog_source: string }>;
    }>();
    expect(body.entries.map((e) => e.id)).toEqual([
      "official-high",
      "official-low",
    ]);
    expect(body.entries.every((e) => e.catalog_source === "official")).toBe(
      true,
    );
  });

  it("GET /registry/skills devolve um catálogo sem seed legado", async () => {
    const ctx = createExecutionContext();
    const req = new Request("https://services.vectora.company/registry/skills");
    const res = await worker.fetch(req, env, ctx);
    await waitOnExecutionContext(ctx);

    expect(res.status).toBe(200);
    const body = await res.json<{ entries: Array<{ id: string }> }>();
    expect(body.entries).toEqual([]);
  });

  it("GET /registry/mcp traduz falha do backend em erro 503", async () => {
    const failingEnv = {
      ...env,
      DB: {
        prepare: () => {
          throw new Error("database unavailable");
        },
      },
    } as unknown as typeof env;
    const ctx = createExecutionContext();
    const res = await worker.fetch(
      new Request("https://services.vectora.company/registry/mcp"),
      failingEnv,
      ctx,
    );
    await waitOnExecutionContext(ctx);

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "registry unavailable" });
  });

  it("GET /registry/extensions continua placeholder (fora de escopo — SDK de extensões não existe)", async () => {
    const ctx = createExecutionContext();
    const req = new Request(
      "https://services.vectora.company/registry/extensions",
    );
    const res = await worker.fetch(req, env, ctx);
    await waitOnExecutionContext(ctx);

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ entries: [] });
  });

  it("reaplicar o seed do mcp_catalog não duplica linhas (INSERT OR IGNORE idempotente)", async () => {
    const before = await env.DB.prepare(
      "SELECT COUNT(*) as n FROM mcp_catalog",
    ).first<{ n: number }>();

    await env.DB.prepare(
      `INSERT OR IGNORE INTO mcp_catalog (id, name, description, install_cmd, env_vars, homepage, category, vectora_verified) VALUES
        ('brave-search', 'Brave Search', 'x', 'npx x', '[]', null, 'web', 1)`,
    ).run();

    const after = await env.DB.prepare(
      "SELECT COUNT(*) as n FROM mcp_catalog",
    ).first<{ n: number }>();

    expect(after?.n).toBe(before?.n);
  });
});
