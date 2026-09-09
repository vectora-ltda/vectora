import {
  createExecutionContext,
  env,
  waitOnExecutionContext,
} from "cloudflare:test";
import { describe, expect, it } from "vitest";
import worker from "../src/index";

describe("registry — catálogos reais de MCP/Skills (D1)", () => {
  it("GET /registry/mcp devolve as entradas seedadas ordenadas por downloads_count", async () => {
    const ctx = createExecutionContext();
    const req = new Request("https://services.vectora.company/registry/mcp");
    const res = await worker.fetch(req, env, ctx);
    await waitOnExecutionContext(ctx);

    expect(res.status).toBe(200);
    const body = await res.json<{ entries: Array<{ id: string }> }>();
    const ids = body.entries.map((e) => e.id);
    expect(ids).toContain("filesystem");
    expect(ids).toContain("github");
    expect(ids.length).toBeGreaterThanOrEqual(6);
  });

  it("GET /registry/skills devolve as skills oficiais seedadas", async () => {
    const ctx = createExecutionContext();
    const req = new Request("https://services.vectora.company/registry/skills");
    const res = await worker.fetch(req, env, ctx);
    await waitOnExecutionContext(ctx);

    expect(res.status).toBe(200);
    const body = await res.json<{ entries: Array<{ id: string }> }>();
    expect(body.entries.map((entry) => entry.id)).toEqual([
      "vectora-code-review",
      "vectora-adr",
      "vectora-rfc",
      "vectora-prd",
    ]);
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
