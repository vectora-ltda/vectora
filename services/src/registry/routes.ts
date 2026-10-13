/**
 * registry/ — "um registry, três catálogos": mcp, skills e extensions são
 * recursos irmãos do mesmo Worker, não três serviços diferentes.
 *
 * `mcp` e `skills` são catálogos reais em D1 (`mcp_catalog`/`skills_catalog`,
 * `migrations/0001_schema.sql`). Curadoria
 * O catálogo MCP público só expõe linhas sincronizadas do GitHub MCP
 * Official MCP Registry (`catalog_source='official'`). Seeds locais e snapshots antigos
 * ficam fora da resposta até serem confirmados pelo próximo snapshot oficial.
 * O cliente Vectora (`backend/services/registry_client.py`) consome esse
 * catálogo único; uma lista vazia é um estado válido, não um fallback para
 * fontes paralelas.
 *
 * Instalações de skills e MCPs só aceitam identificadores já presentes nos
 * catálogos validados. Não há publicação pública nem formulário de entrada
 * manual no produto.
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
  try {
    const stmt = c.env.DB.prepare(
      `SELECT id, name, description, install_cmd, env_vars, homepage, category, vectora_verified, icon_url, publisher, publisher_url, stars_count, downloads_count, runtime_hint, package_identifier, transport, server_url, catalog_source, updated_at FROM mcp_catalog WHERE catalog_status = 'active' AND catalog_source = 'official'${where.length ? ` AND ${where.join(" AND ")}` : ""} ORDER BY stars_count DESC, downloads_count DESC, name COLLATE NOCASE`,
    );
    const { results } = await (
      params.length ? stmt.bind(...params) : stmt
    ).all();
    return c.json({ entries: results ?? [] });
  } catch (error) {
    console.error("registry mcp query failed", error);
    return c.json({ error: "registry unavailable" }, 503);
  }
});

registry.get("/status/:source", async (c) => {
  const source = c.req.param("source");
  if (source !== "mcp" && source !== "skills") {
    return c.json({ error: "invalid source" }, 400);
  }
  try {
    const state = await c.env.DB.prepare(
      "SELECT status, last_synced_at, last_error FROM registry_sync_state WHERE source = ?",
    )
      .bind(source)
      .first<{
        status: string;
        last_synced_at: string | null;
        last_error: string | null;
      }>();
    return c.json({
      source,
      status: state?.status ?? "never",
      last_synced_at: state?.last_synced_at ?? null,
      error: state?.last_error?.split(":", 1)[0] ?? null,
    });
  } catch (error) {
    console.error("registry status query failed", { source, error });
    return c.json(
      {
        source,
        status: "unavailable",
        last_synced_at: null,
        error: "status unavailable",
      },
      503,
    );
  }
});

const SKILLS_COLUMNS =
  "id, name, description, source, package_name, version, tags, category, vectora_verified, publisher_id, (SELECT NULLIF(TRIM(full_name), '') FROM users WHERE users.id = skills_catalog.publisher_id) AS publisher, verified, downloads_count, updated_at";

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

const EXTENSION_COLUMNS = `e.id, e.name, e.description, e.readme, e.homepage,
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
  const existingPublisher = await c.env.DB.prepare(
    "SELECT id, owner_user_id FROM vext_publishers WHERE fingerprint = ?",
  )
    .bind(fingerprint)
    .first<{ id: string; owner_user_id: string }>();
  if (existingPublisher && existingPublisher.owner_user_id !== userId)
    return c.json({ error: "publisher_fingerprint_owned" }, 409);
  if (existingPublisher) {
    await c.env.DB.prepare(
      "UPDATE vext_publishers SET name = ?, public_key = ?, updated_at = datetime('now') WHERE id = ? AND owner_user_id = ?",
    )
      .bind(name, publicKey, existingPublisher.id, userId)
      .run();
  } else {
    await c.env.DB.prepare(
      "INSERT INTO vext_publishers (id, owner_user_id, name, public_key, fingerprint) VALUES (?, ?, ?, ?, ?)",
    )
      .bind(crypto.randomUUID(), userId, name, publicKey, fingerprint)
      .run();
  }
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

registry.get("/extensions/:id/readme", async (c) => {
  const row = await c.env.DB.prepare(
    `SELECT e.id, e.name, e.readme, v.version FROM vext_extensions e
      JOIN vext_publishers p ON p.id = e.publisher_id
      JOIN vext_versions v ON v.extension_id = e.id
      WHERE e.id = ? AND v.status = 'published' AND e.revoked = 0 AND p.revoked = 0
      ORDER BY v.created_at DESC LIMIT 1`,
  )
    .bind(c.req.param("id"))
    .first<{ id: string; name: string; readme: string; version: string }>();
  if (!row) return c.json({ error: "not_found" }, 404);
  return c.json({
    id: row.id,
    name: row.name,
    version: row.version,
    content: row.readme,
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
    readme = text("readme"),
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
    !readme ||
    !version ||
    !fingerprint ||
    !signature ||
    !digest ||
    !manifest ||
    !integrity ||
    !["node", "python", "none"].includes(runtime)
  )
    return c.json({ error: "invalid_metadata" }, 400);
  let manifestPayload: Record<string, unknown>;
  try {
    const parsed = JSON.parse(manifest) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
      throw new Error("manifest must be an object");
    manifestPayload = parsed as Record<string, unknown>;
    const manifestId = manifestPayload.id;
    if (
      typeof manifestId !== "string" ||
      !manifestId ||
      manifestPayload.name !== name ||
      manifestPayload.version !== version ||
      manifestPayload.runtime !== runtime
    )
      return c.json({ error: "manifest_mismatch" }, 422);
    JSON.parse(integrity);
  } catch {
    return c.json({ error: "invalid_manifest" }, 400);
  }
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
  const extensionId = manifestPayload.id as string;
  const existingExtension = await c.env.DB.prepare(
    "SELECT id, publisher_id, name FROM vext_extensions WHERE id = ?",
  )
    .bind(extensionId)
    .first<{ id: string; publisher_id: string; name: string }>();
  if (
    existingExtension &&
    (existingExtension.publisher_id !== publisher.id ||
      existingExtension.name !== name)
  )
    return c.json({ error: "extension_id_conflict" }, 409);
  const extensionByName = await c.env.DB.prepare(
    "SELECT id, publisher_id FROM vext_extensions WHERE publisher_id = ? AND name = ?",
  )
    .bind(publisher.id, name)
    .first<{ id: string; publisher_id: string }>();
  if (extensionByName && extensionByName.id !== extensionId)
    return c.json({ error: "extension_id_conflict" }, 409);
  const versionId = crypto.randomUUID();
  const r2Key = `vext/${publisher.id}/${extensionId}/${version}/${digest}.vext`;
  await c.env.R2.put(r2Key, bytes, {
    httpMetadata: { contentType: "application/vnd.vectora.vext+zip" },
    customMetadata: { digest, publisher: publisher.id },
  });
  try {
    await c.env.DB.batch([
      c.env.DB.prepare(
        "INSERT INTO vext_extensions (id, publisher_id, name, description, readme) VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET description = excluded.description, readme = excluded.readme, updated_at = datetime('now')",
      ).bind(extensionId, publisher.id, name, description, readme),
      c.env.DB.prepare(
        "INSERT INTO vext_versions (id, extension_id, version, api_version, protocol_version, runtime, platforms, permissions, size_bytes, digest, r2_key, signature, signature_verified, status) VALUES (?, ?, ?, 1, 1, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
      ).bind(
        versionId,
        extensionId,
        version,
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
  const extension = await c.env.DB.prepare(
    "SELECT id FROM vext_extensions WHERE publisher_id = ? AND name = ?",
  )
    .bind(publisher.id, name)
    .first<{ id: string }>();
  if (!extension) return c.json({ error: "extension_create_failed" }, 500);
  return c.json(
    {
      ok: true,
      id: extension.id,
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
    .first<{
      id: string;
      r2_key: string;
      signature: string;
      signature_verified: number;
    }>();
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
