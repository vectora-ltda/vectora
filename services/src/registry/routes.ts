/**
 * registry/ — "um registry, três catálogos": mcp, skills e extensions são
 * recursos irmãos do mesmo Worker, não três serviços diferentes.
 *
 * `mcp` e `skills` são catálogos reais em D1 (`mcp_catalog`/`skills_catalog`,
 * `migrations/0001_schema.sql`). Curadoria
 * manual (`catalog_source='curated'`) entra por revisão do catálogo — mas o
 * catálogo MCP também é populado automaticamente pelo cron `scheduled()`
 * (`discovery.ts`, `catalog_source='github'`), que nunca sobrescreve uma
 * linha curada. O cliente Vectora (`backend/services/registry_client.py`)
 * consome esse catálogo único; uma lista vazia é um estado válido, não um
 * fallback para fontes paralelas.
 *
 * Instalações de skills e MCPs só aceitam identificadores já presentes nos
 * catálogos validados. Não há publicação pública nem formulário de entrada
 * manual no produto.
 *
 * `extensions` continua placeholder — depende do SDK de autoria
 * (`vectora_ext` Python, `@vectora/extension-sdk` TS) e do Extension Host,
 * nenhum dos dois existe ainda.
 */
import { Hono } from "hono";
import type { Env } from "../gateway/types";
import { requireAdmin } from "../auth/roles";
import { compareVersions, latestPerPackage } from "../lib/versioning";

export const registry = new Hono<{ Bindings: Env }>();

/** `?q=` casa em name/description (LIKE, case-insensitive via `nocase`). */
function buildSearchClause(
  q: string | undefined,
  columns: string[],
): { clause: string; params: string[] } {
  if (!q) return { clause: "", params: [] };
  const like = `%${q}%`;
  const clause = columns.map((c) => `${c} LIKE ? COLLATE NOCASE`).join(" OR ");
  return { clause: `(${clause})`, params: columns.map(() => like) };
}

registry.get("/mcp", async (c) => {
  const q = c.req.query("q");
  const category = c.req.query("category");

  const where: string[] = [];
  const params: string[] = [];
  const search = buildSearchClause(q, ["name", "description"]);
  if (search.clause) {
    where.push(search.clause);
    params.push(...search.params);
  }
  if (category) {
    where.push("category = ?");
    params.push(category);
  }
  const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

  const stmt = c.env.DB.prepare(
    `SELECT id, name, description, install_cmd, env_vars, homepage, category, vectora_verified, icon_url, publisher, publisher_url, stars_count, downloads_count, runtime_hint, package_identifier, transport, server_url, catalog_source, updated_at FROM mcp_catalog ${whereSql} ORDER BY stars_count DESC, downloads_count DESC, name COLLATE NOCASE`,
  );
  const { results } = await (params.length ? stmt.bind(...params) : stmt).all();
  return c.json({ entries: results ?? [] });
});

const SKILLS_COLUMNS =
  "id, name, description, source, package_name, version, tags, category, vectora_verified, publisher_id, (SELECT COALESCE(full_name, email) FROM users WHERE users.id = skills_catalog.publisher_id) AS publisher, verified, downloads_count, updated_at";

interface SkillRow {
  id: string;
  name: string;
  package_name: string | null;
  version: string;
  [key: string]: unknown;
}

registry.get("/skills", async (c) => {
  const q = c.req.query("q");
  const category = c.req.query("category");
  const tag = c.req.query("tags");

  const where: string[] = [
    "COALESCE(package_name, '') NOT LIKE '@vectora/%'",
    "id NOT LIKE 'vectora/%'",
    "id NOT LIKE 'vectora-%'",
  ];
  const params: string[] = [];
  const search = buildSearchClause(q, ["name", "description"]);
  if (search.clause) {
    where.push(search.clause);
    params.push(...search.params);
  }
  if (category) {
    where.push("category = ?");
    params.push(category);
  }
  if (tag) {
    // tags é JSON array serializado — LIKE sobre o texto bruto basta pra
    // uma tag simples, sem precisar de JSON1 (`json_each`) pra esse caso.
    where.push("tags LIKE ? COLLATE NOCASE");
    params.push(`%"${tag}"%`);
  }
  const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

  const stmt = c.env.DB.prepare(
    `SELECT ${SKILLS_COLUMNS} FROM skills_catalog ${whereSql} ORDER BY downloads_count DESC`,
  );
  const { results } = await (
    params.length ? stmt.bind(...params) : stmt
  ).all<SkillRow>();
  return c.json({ entries: latestPerPackage(results ?? []) });
});

/** Lista todas as versões publicadas de um `package_name` de skill. */
registry.get("/skills/:name/versions", async (c) => {
  const packageName = c.req.param("name");
  const { results } = await c.env.DB.prepare(
    `SELECT ${SKILLS_COLUMNS} FROM skills_catalog WHERE package_name = ? AND package_name NOT LIKE '@vectora/%'`,
  )
    .bind(packageName)
    .all<SkillRow>();
  const sorted = [...(results ?? [])].sort((a, b) =>
    compareVersions(b.version, a.version),
  );
  return c.json({ entries: sorted });
});

registry.get("/extensions", (c) => c.json({ entries: [] }));

/**
 * Publica uma skill pra o catálogo comunitário — `source` é sempre uma URL
 * git (nunca upload), reaproveitando o mesmo mecanismo de instalação já
 * usado por `backend/workspace/skills.py`. Grava com `verified=0`, curadoria
 * manual posterior via `PATCH /admin/skills/:id/verify`.
 */
/** Curadoria: seta `verified=1` — só quem tem `role='admin'`. */
registry.patch("/admin/skills/:id/verify", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);

  const id = c.req.param("id");
  const row = await c.env.DB.prepare(
    "SELECT id FROM skills_catalog WHERE id = ?",
  )
    .bind(id)
    .first();
  if (!row) return c.json({ error: "not_found" }, 404);

  await c.env.DB.prepare("UPDATE skills_catalog SET verified = 1 WHERE id = ?")
    .bind(id)
    .run();

  return c.json({ ok: true, id, verified: true });
});
