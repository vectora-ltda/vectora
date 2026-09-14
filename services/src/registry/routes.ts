/**
 * registry/ — "um registry, três catálogos": mcp, skills e extensions são
 * recursos irmãos do mesmo Worker, não três serviços diferentes.
 *
 * `mcp` e `skills` são catálogos reais em D1 (`mcp_catalog`/`skills_catalog`,
 * `migrations/0001_schema.sql`). Curadoria
 * manual (`catalog_source='curated'`) entra via PR editando o seed — mas o
 * catálogo também é populado automaticamente pelo cron `scheduled()`
 * (`discovery.ts`, `catalog_source='official'|'github'`), que nunca
 * sobrescreve uma linha curada. O cliente Vectora (`backend/services/
 * registry_client.py`) já sabe cair pro fallback local/hardcoded quando o
 * registry remoto está vazio ou fora do ar — lista vazia aqui é um estado
 * válido, não erro.
 *
 * `POST /skills` abre publicação de skills à comunidade — padrão
 * convergente dos registries reais (SkillRegistry.io, OpenAgentSkill,
 * Vercel Agent Skills): unidade de distribuição é uma URL de repositório
 * git, não upload de blob — o Vectora clona sob demanda na instalação
 * (`backend/workspace/skills.py`), este endpoint só registra a URL no
 * catálogo com `verified=0` até curadoria de admin. MCP catalog
 * deliberadamente NÃO ganha publish — instalar código de terceiro tem
 * modelo de confiança mais pesado que instalar um `SKILL.md`; curadoria
 * fechada por design.
 *
 * `extensions` usa o mesmo Worker, mas mantém bytes imutáveis no R2 e
 * metadados, estados e publishers no D1. A publicação exige assinatura
 * Ed25519 verificável antes de entrar na fila de curadoria.
 */
import { Hono } from "hono";
import type { Env } from "../gateway/types";
import { requireAdmin } from "../auth/roles";
import { requireUserId } from "../auth/routes";
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
    `SELECT id, name, description, install_cmd, env_vars, homepage, category, vectora_verified, icon_url, downloads_count, updated_at FROM mcp_catalog ${whereSql} ORDER BY downloads_count DESC`,
  );
  const { results } = await (params.length ? stmt.bind(...params) : stmt).all();
  return c.json({ entries: results ?? [] });
});

const SKILLS_COLUMNS =
  "id, name, description, source, package_name, version, tags, category, vectora_verified, publisher_id, verified, downloads_count, updated_at";

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
    `SELECT ${SKILLS_COLUMNS} FROM skills_catalog WHERE package_name = ?`,
  )
    .bind(packageName)
    .all<SkillRow>();
  const sorted = [...(results ?? [])].sort((a, b) =>
    compareVersions(b.version, a.version),
  );
  return c.json({ entries: sorted });
});

const EXTENSION_COLUMNS = `e.id, e.name, e.description, e.homepage,
  e.vectora_verified, e.revoked AS extension_revoked, p.name AS publisher,
  p.fingerprint, v.id AS version_id, v.version, v.api_version,
  v.protocol_version, v.runtime, v.platforms, v.permissions, v.dependencies,
  v.changelog, v.size_bytes, v.digest, v.status, v.created_at`;

function decodeBase64(value: string): Uint8Array | null {
  try {
    const binary = atob(value);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  } catch {
    return null;
  }
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  return `{${Object.entries(value as Record<string, unknown>)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
    .join(",")}}`;
}

async function verifyPublisherSignature(
  publicKey: string,
  signature: string,
  manifestText: string,
  integrityText: string,
): Promise<boolean> {
  const keyBytes = decodeBase64(publicKey);
  const signatureBytes = decodeBase64(signature);
  if (!keyBytes || keyBytes.length !== 32 || !signatureBytes) return false;
  let manifest: unknown;
  let integrity: unknown;
  try {
    manifest = JSON.parse(manifestText);
    integrity = JSON.parse(integrityText);
  } catch {
    return false;
  }
  const key = await crypto.subtle.importKey(
    "raw",
    keyBytes,
    { name: "Ed25519" },
    false,
    ["verify"],
  );
  return crypto.subtle.verify(
    { name: "Ed25519" },
    key,
    signatureBytes,
    new TextEncoder().encode(canonicalJson({ integrity, manifest })),
  );
}

registry.post("/extensions/publishers", async (c) => {
  const userId = await requireUserId(c);
  if (!userId) return c.json({ error: "unauthorized" }, 401);
  const body = (await c.req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  const name = typeof body?.name === "string" ? body.name.trim() : "";
  const publicKey =
    typeof body?.public_key === "string" ? body.public_key.trim() : "";
  const fingerprint =
    typeof body?.fingerprint === "string"
      ? body.fingerprint.trim().toLowerCase()
      : "";
  if (!name || !publicKey || !/^[a-f0-9]{64}$/.test(fingerprint))
    return c.json({ error: "invalid_publisher" }, 400);
  let keyBytes: Uint8Array;
  try {
    const binary = atob(publicKey);
    keyBytes = Uint8Array.from(binary, (value) => value.charCodeAt(0));
  } catch {
    return c.json({ error: "invalid_public_key" }, 400);
  }
  if (keyBytes.length !== 32)
    return c.json({ error: "invalid_public_key" }, 400);
  const digest = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", keyBytes)),
    (value) => value.toString(16).padStart(2, "0"),
  ).join("");
  if (digest !== fingerprint)
    return c.json({ error: "fingerprint_mismatch" }, 422);
  const id = crypto.randomUUID();
  await c.env.DB.prepare(
    `INSERT INTO vext_publishers (id, owner_user_id, name, public_key, fingerprint)
     VALUES (?, ?, ?, ?, ?) ON CONFLICT(fingerprint) DO UPDATE SET name = excluded.name, updated_at = datetime('now')`,
  )
    .bind(id, userId, name, publicKey, fingerprint)
    .run();
  const row = await c.env.DB.prepare(
    "SELECT id, name, fingerprint, revoked FROM vext_publishers WHERE owner_user_id = ? AND fingerprint = ?",
  )
    .bind(userId, fingerprint)
    .first();
  return c.json({ publisher: row }, 201);
});

registry.get("/extensions", async (c) => {
  const q = c.req.query("q");
  const like = q ? `%${q}%` : null;
  const { results } = await c.env.DB.prepare(
    `SELECT ${EXTENSION_COLUMNS} FROM vext_extensions e
      JOIN vext_publishers p ON p.id = e.publisher_id
      JOIN vext_versions v ON v.extension_id = e.id
      WHERE v.status = 'published' AND e.revoked = 0 AND p.revoked = 0
        AND (? IS NULL OR e.name LIKE ? COLLATE NOCASE OR e.description LIKE ? COLLATE NOCASE)
        AND v.created_at = (SELECT MAX(v2.created_at) FROM vext_versions v2 WHERE v2.extension_id = e.id AND v2.status = 'published')
      ORDER BY e.name COLLATE NOCASE`,
  )
    .bind(like, like, like)
    .all();
  return c.json({ entries: results ?? [] });
});

registry.get("/extensions/:id/versions", async (c) => {
  const { results } = await c.env.DB.prepare(
    `SELECT ${EXTENSION_COLUMNS} FROM vext_extensions e
      JOIN vext_publishers p ON p.id = e.publisher_id
      JOIN vext_versions v ON v.extension_id = e.id
      WHERE e.id = ? AND v.status = 'published' AND e.revoked = 0 AND p.revoked = 0
      ORDER BY v.created_at DESC`,
  )
    .bind(c.req.param("id"))
    .all();
  return c.json({ entries: results ?? [] });
});

registry.get("/extensions/:id/download/:version", async (c) => {
  const row = await c.env.DB.prepare(
    `SELECT v.r2_key, v.digest FROM vext_versions v
      JOIN vext_extensions e ON e.id = v.extension_id
      JOIN vext_publishers p ON p.id = e.publisher_id
      WHERE e.id = ? AND v.version = ? AND v.status = 'published'
        AND e.revoked = 0 AND p.revoked = 0`,
  )
    .bind(c.req.param("id"), c.req.param("version"))
    .first<{ r2_key: string; digest: string }>();
  if (!row) return c.json({ error: "not_found" }, 404);
  const object = await c.env.R2.get(row.r2_key);
  if (!object) return c.json({ error: "artifact_unavailable" }, 503);
  return new Response(object.body, {
    headers: {
      "Content-Type": "application/vnd.vectora.vext+zip",
      "Content-Length": String(object.size),
      ETag: object.httpEtag,
      "X-VEXT-Digest": row.digest,
      "Cache-Control": "public, max-age=31536000, immutable",
    },
  });
});

registry.post("/extensions/publish", async (c) => {
  const userId = await requireUserId(c);
  if (!userId) return c.json({ error: "unauthorized" }, 401);
  if (!(c.req.header("Content-Type") ?? "").includes("multipart/form-data"))
    return c.json({ error: "invalid_content_type" }, 400);
  const body = await c.req.parseBody();
  const artifact = body.artifact;
  const text = (key: string) =>
    typeof body[key] === "string" ? body[key].trim() : "";
  if (!(artifact instanceof File) || artifact.size === 0)
    return c.json({ error: "artifact_required" }, 400);
  const name = text("name"),
    description = text("description"),
    version = text("version");
  const fingerprint = text("fingerprint"),
    signature = text("signature"),
    digest = text("digest"),
    manifest = text("manifest"),
    integrity = text("integrity");
  const runtime = text("runtime");
  if (
    !name ||
    !description ||
    !version ||
    !fingerprint ||
    !signature ||
    !digest ||
    !manifest ||
    !integrity ||
    !["node", "python", "none"].includes(runtime)
  )
    return c.json({ error: "invalid_metadata" }, 400);
  const bytes = await artifact.arrayBuffer();
  const actualDigest = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (value) => value.toString(16).padStart(2, "0"),
  ).join("");
  if (actualDigest !== digest) return c.json({ error: "digest_mismatch" }, 422);
  const publisher = await c.env.DB.prepare(
    "SELECT id, revoked FROM vext_publishers WHERE owner_user_id = ? AND fingerprint = ?",
  )
    .bind(userId, fingerprint)
    .first<{ id: string; revoked: number }>();
  if (!publisher || publisher.revoked)
    return c.json({ error: "publisher_untrusted" }, 403);
  const publisherKey = await c.env.DB.prepare(
    "SELECT public_key FROM vext_publishers WHERE id = ?",
  )
    .bind(publisher.id)
    .first<{ public_key: string }>();
  if (
    !publisherKey ||
    !(await verifyPublisherSignature(
      publisherKey.public_key,
      signature,
      manifest,
      integrity,
    ))
  )
    return c.json({ error: "invalid_signature" }, 422);
  const extensionId = crypto.randomUUID();
  const versionId = crypto.randomUUID();
  const r2Key = `vext/${publisher.id}/${name}/${version}/${digest}.vext`;
  await c.env.R2.put(r2Key, bytes, {
    httpMetadata: { contentType: "application/vnd.vectora.vext+zip" },
    customMetadata: { digest, publisher: publisher.id },
  });
  try {
    await c.env.DB.batch([
      c.env.DB.prepare(
        "INSERT INTO vext_extensions (id, publisher_id, name, description) VALUES (?, ?, ?, ?) ON CONFLICT(publisher_id, name) DO UPDATE SET description = excluded.description, updated_at = datetime('now')",
      ).bind(extensionId, publisher.id, name, description),
      c.env.DB.prepare(
        "INSERT INTO vext_versions (id, extension_id, version, api_version, protocol_version, runtime, platforms, permissions, size_bytes, digest, r2_key, signature, signature_verified, status) VALUES (?, (SELECT id FROM vext_extensions WHERE publisher_id = ? AND name = ?), ?, 1, 1, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
      ).bind(
        versionId,
        publisher.id,
        name,
        version,
        runtime,
        text("platforms") || '["any"]',
        text("permissions") || "[]",
        artifact.size,
        digest,
        r2Key,
        signature,
        1,
      ),
    ]);
  } catch (error) {
    await c.env.R2.delete(r2Key);
    throw error;
  }
  return c.json(
    {
      ok: true,
      id: extensionId,
      version_id: versionId,
      digest,
      status: "pending",
    },
    201,
  );
});

registry.patch("/admin/extensions/:id/:version/publish", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);
  const row = await c.env.DB.prepare(
    `SELECT v.id, v.r2_key, v.signature, v.signature_verified FROM vext_versions v
      JOIN vext_extensions e ON e.id = v.extension_id
      WHERE e.id = ? AND v.version = ? AND v.status = 'pending'`,
  )
    .bind(c.req.param("id"), c.req.param("version"))
    .first<{ id: string; r2_key: string; signature: string; signature_verified: number }>();
  if (!row) return c.json({ error: "not_found" }, 404);
  if (
    !row.signature ||
    row.signature_verified !== 1 ||
    !(await c.env.R2.head(row.r2_key))
  )
    return c.json({ error: "artifact_unavailable" }, 422);
  await c.env.DB.prepare(
    "UPDATE vext_versions SET status = 'published', published_at = datetime('now') WHERE id = ?",
  )
    .bind(row.id)
    .run();
  return c.json({ ok: true, id: row.id, status: "published" });
});

registry.patch("/admin/extensions/:id/:version/revoke", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);
  const result = await c.env.DB.prepare(
    `UPDATE vext_versions SET status = 'revoked' WHERE id = (SELECT v.id FROM vext_versions v WHERE v.extension_id = ? AND v.version = ?)`,
  )
    .bind(c.req.param("id"), c.req.param("version"))
    .run();
  if (!result.meta.changes) return c.json({ error: "not_found" }, 404);
  return c.json({
    ok: true,
    id: c.req.param("id"),
    version: c.req.param("version"),
    status: "revoked",
  });
});

/**
 * Publica uma skill pra o catálogo comunitário — `source` é sempre uma URL
 * git (nunca upload), reaproveitando o mesmo mecanismo de instalação já
 * usado por `backend/workspace/skills.py`. Grava com `verified=0`, curadoria
 * manual posterior via `PATCH /admin/skills/:id/verify`.
 */
registry.post("/skills", async (c) => {
  const userId = await requireUserId(c);
  if (!userId) return c.json({ error: "unauthorized" }, 401);

  const body = await c.req.json().catch(() => null);
  if (!body || typeof body !== "object") {
    return c.json({ error: "invalid_body" }, 400);
  }

  const name = typeof body.name === "string" ? body.name.trim() : "";
  const description =
    typeof body.description === "string" ? body.description.trim() : "";
  const source = typeof body.source === "string" ? body.source.trim() : "";
  const category =
    typeof body.category === "string" && body.category.trim()
      ? body.category.trim()
      : null;
  const tags = Array.isArray(body.tags)
    ? body.tags.filter((t: unknown): t is string => typeof t === "string")
    : [];
  const version =
    typeof body.version === "string" && body.version.trim()
      ? body.version.trim()
      : "0.0.1";
  const packageName =
    typeof body.package_name === "string" && body.package_name.trim()
      ? body.package_name.trim()
      : name;

  if (!name) return c.json({ error: "invalid_name" }, 400);
  if (!description) return c.json({ error: "invalid_description" }, 400);
  if (!isGitUrl(source)) return c.json({ error: "invalid_source" }, 400);

  const id = crypto.randomUUID();
  await c.env.DB.prepare(
    `INSERT INTO skills_catalog
       (id, name, description, source, package_name, version, tags, category, catalog_source, publisher_id, verified)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'community', ?, 0)`,
  )
    .bind(
      id,
      name,
      description,
      source,
      packageName,
      version,
      JSON.stringify(tags),
      category,
      userId,
    )
    .run();

  return c.json({
    ok: true,
    id,
    status: "published",
    verified: false,
    version,
    package_name: packageName,
  });
});

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

/** Só http(s)://.../repo(.git) ou `git@host:owner/repo.git` — mesma
 * validação superficial de esquema/host que `backend/workspace/skills.py`
 * já exige antes de tentar clonar; o clone real (que valida de verdade se
 * é um repo git) só acontece na instalação, não aqui. */
function isGitUrl(value: string): boolean {
  if (!value) return false;
  if (/^git@[\w.-]+:[\w./-]+\.git$/.test(value)) return true;
  try {
    const url = new URL(value);
    return (
      (url.protocol === "https:" || url.protocol === "http:") && !!url.hostname
    );
  } catch {
    return false;
  }
}
