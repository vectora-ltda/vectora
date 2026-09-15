import { env } from "cloudflare:test";
import { beforeAll } from "vitest";
// @ts-expect-error — Vite `?raw` import, resolvido em build/test time (esbuild).
import schemaSql from "../migrations/0001_schema.sql?raw";

const _realFetch: typeof fetch = globalThis.fetch;
globalThis.fetch = function guardedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  if (url.startsWith("https://api.resend.com")) return Promise.resolve(new Response(null, { status: 200 }));
  if (url.startsWith("https://registry.modelcontextprotocol.io")) return Promise.resolve(new Response(JSON.stringify({ servers: [], metadata: {} }), { status: 200, headers: { "Content-Type": "application/json" } }));
  return _realFetch(input, init);
} as typeof fetch;

async function applyMigration(sql: string): Promise<void> {
  const statements = sql.split("\n").filter((line) => !line.trim().startsWith("--")).join("\n").split(";").map((s) => s.trim()).filter(Boolean);
  for (const statement of statements) await env.DB.prepare(statement).run();
}

beforeAll(async () => {
  await applyMigration(schemaSql as string);
  try {
    await env.DB.prepare("ALTER TABLE vext_extensions ADD COLUMN readme TEXT NOT NULL DEFAULT ''").run();
  } catch {
    // The worker pool may reuse a database already initialized by an older test run.
  }
});
