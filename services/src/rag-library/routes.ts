/**
 * rag-library/ — catálogo de bancos RAG pré-indexados, dois tipos de
 * linha na mesma tabela `rag_packages`:
 *
 * - First-party: bibliotecas de código pré-indexadas pela Vectora
 *   (`source_lib`/`source_version`, ex. "requests 2.31.0"), sem publisher.
 * - Comunidade (Memory Library): buckets publicados por usuários via
 *   `POST /publish`, com `publisher_id`/`embed_model`/`license`, curados
 *   via `PATCH /admin/:id/verify` (community aberta + selo first-party).
 *
 * Storage é R2 (mesmo bucket que `issues/routes.ts` já usa) — revoga a
 * decisão antiga de storage externo (Backblaze B2): não era necessário,
 * R2 já está provisionado neste Worker.
 */
import { Hono } from "hono";
import type { Env } from "../gateway/types";
import { enqueueJob } from "../lib/queue";
import { requireAdmin } from "../auth/roles";
import { requireUserId } from "../auth/routes";
import { compareVersions, latestPerPackage } from "../lib/versioning";

export const ragLibrary = new Hono<{ Bindings: Env }>();

/**
 * Motivo fixo de falha do reindex — não existe provedor de storage externo
 * configurado ainda. Marcar como "failed" com esse motivo é o
 * comportamento honesto pro estado atual da infra: o job roda de verdade,
 * só não tem onde baixar/indexar nada ainda.
 */
export const NO_STORAGE_PROVIDER_REASON =
  "Provedor de storage externo ainda não configurado";

interface RagPackageRow {
  id: string;
  name: string;
  description: string | null;
  source_lib: string;
  source_version: string;
  package_name: string | null;
  version: string;
  size_bytes: number;
  checksum: string;
  storage_url: string;
  embed_model: string | null;
  publisher_id: string | null;
  publisher: string | null;
  verified: number;
  downloads_count: number;
  license: string | null;
  updated_at: string;
}

const RAG_PACKAGE_COLUMNS =
  "id, name, description, source_lib, source_version, package_name, version, size_bytes, checksum, embed_model, publisher_id, (SELECT COALESCE(full_name, email) FROM users WHERE users.id = rag_packages.publisher_id) AS publisher, verified, downloads_count, license, updated_at";

ragLibrary.get("/", async (c) => {
  const q = c.req.query("q");
  const base = `SELECT ${RAG_PACKAGE_COLUMNS} FROM rag_packages`;
  if (!q) {
    const { results } = await c.env.DB.prepare(
      `${base} ORDER BY name`,
    ).all<RagPackageRow>();
    return c.json(
      latestPerPackage(results).sort((a, b) => a.name.localeCompare(b.name)),
    );
  }
  const like = `%${q}%`;
  const { results } = await c.env.DB.prepare(
    `${base} WHERE name LIKE ? COLLATE NOCASE OR description LIKE ? COLLATE NOCASE ORDER BY name`,
  )
    .bind(like, like)
    .all<RagPackageRow>();
  return c.json(
    latestPerPackage(results).sort((a, b) => a.name.localeCompare(b.name)),
  );
});

/** Lista todas as versões publicadas de um `package_name`, mais recente primeiro. */
ragLibrary.get("/:name/versions", async (c) => {
  const packageName = c.req.param("name");
  const { results } = await c.env.DB.prepare(
    `SELECT ${RAG_PACKAGE_COLUMNS} FROM rag_packages WHERE package_name = ?`,
  )
    .bind(packageName)
    .all<RagPackageRow>();
  const sorted = [...results].sort((a, b) =>
    compareVersions(b.version, a.version),
  );
  return c.json(sorted);
});

/** Serve o binário de um bucket publicado — mesmo padrão de `issues/routes.ts`. */
ragLibrary.get("/files/*", async (c) => {
  const key = c.req.path.replace(/^.*?\/files\//, "");
  if (!key.startsWith("rag-library/")) return c.text("not found", 404);
  const obj = await c.env.R2.get(key);
  if (!obj) return c.text("not found", 404);
  return new Response(obj.body, {
    headers: {
      "Content-Type":
        obj.httpMetadata?.contentType ?? "application/octet-stream",
      "Cache-Control": "public, max-age=3600",
      ETag: obj.httpEtag,
    },
  });
});

ragLibrary.get("/:id/download", async (c) => {
  const id = c.req.param("id");
  const row = await c.env.DB.prepare(
    "SELECT storage_url FROM rag_packages WHERE id = ?",
  )
    .bind(id)
    .first<Pick<RagPackageRow, "storage_url">>();
  if (!row) return c.json({ error: "not_found" }, 404);

  await c.env.DB.prepare(
    "UPDATE rag_packages SET downloads_count = downloads_count + 1 WHERE id = ?",
  )
    .bind(id)
    .run();

  return c.redirect(row.storage_url, 302);
});

/**
 * Publica um bucket de Memory Library — multipart (name, description,
 * embed_model, license, file, version opcional). `embed_model` é
 * obrigatório aqui (só pras publicações novas — linhas first-party
 * legadas continuam com o campo NULL, sem quebrar). Grava no R2 (mesmo
 * padrão de `issues/routes.ts`) e insere a linha em `rag_packages` com
 * `verified=0` (curadoria manual posterior via `PATCH /admin/:id/verify`).
 *
 * `package_name` (chave de agrupamento entre versões) default pro próprio
 * `name` normalizado — sem isso, cada publish vira um pacote isolado
 * mesmo quando é uma atualização do mesmo bucket (ver `GET /`/`GET /:name/
 * versions`, que agrupam por essa chave).
 */
ragLibrary.post("/publish", async (c) => {
  const userId = await requireUserId(c);
  if (!userId) return c.json({ error: "unauthorized" }, 401);

  const contentType = c.req.header("Content-Type") ?? "";
  if (!contentType.includes("multipart/form-data")) {
    return c.json({ error: "invalid_content_type" }, 400);
  }
  const body = await c.req.parseBody();

  const name = typeof body.name === "string" ? body.name.trim() : "";
  const description =
    typeof body.description === "string" ? body.description.trim() : "";
  const embedModel =
    typeof body.embed_model === "string" ? body.embed_model.trim() : "";
  const license = typeof body.license === "string" ? body.license.trim() : "";
  const version =
    typeof body.version === "string" && body.version.trim()
      ? body.version.trim()
      : "0.0.1";
  const packageName =
    typeof body.package_name === "string" && body.package_name.trim()
      ? body.package_name.trim()
      : name;
  const file = body.file instanceof File ? body.file : null;

  if (!name) return c.json({ error: "invalid_name" }, 400);
  if (!embedModel) return c.json({ error: "embed_model_required" }, 400);
  if (!file) return c.json({ error: "file_required" }, 400);

  const id = crypto.randomUUID();
  const key = `rag-library/${id}/${file.name.replace(/[^\w.-]/g, "_").slice(0, 80)}`;
  const buffer = await file.arrayBuffer();
  const checksum = await sha256Hex(buffer);

  await c.env.R2.put(key, buffer, {
    httpMetadata: { contentType: file.type || "application/octet-stream" },
  });

  const storageUrl = `https://services.vectora.company/rag-library/files/${key}`;

  await c.env.DB.prepare(
    `INSERT INTO rag_packages
       (id, name, source_lib, source_version, package_name, version, size_bytes,
        checksum, storage_url, embed_model, publisher_id, verified, license, description)
     VALUES (?, ?, '', '', ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)`,
  )
    .bind(
      id,
      name,
      packageName,
      version,
      file.size,
      checksum,
      storageUrl,
      embedModel,
      userId,
      license || null,
      description || null,
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
ragLibrary.patch("/admin/:id/verify", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);

  const id = c.req.param("id");
  const row = await c.env.DB.prepare("SELECT id FROM rag_packages WHERE id = ?")
    .bind(id)
    .first();
  if (!row) return c.json({ error: "not_found" }, 404);

  await c.env.DB.prepare("UPDATE rag_packages SET verified = 1 WHERE id = ?")
    .bind(id)
    .run();

  return c.json({ ok: true, id, verified: true });
});

/**
 * Dispara a reindexação de um pacote existente. Marca `status='pending'`
 * na hora (síncrono) e enfileira o job de verdade — o consumer é quem
 * decide o resultado (hoje sempre `failed`, ver NO_STORAGE_PROVIDER_REASON).
 */
ragLibrary.post("/:id/reindex", async (c) => {
  const id = c.req.param("id");
  const row = await c.env.DB.prepare("SELECT id FROM rag_packages WHERE id = ?")
    .bind(id)
    .first();
  if (!row) return c.json({ error: "not_found" }, 404);

  await c.env.DB.prepare(
    "UPDATE rag_packages SET status = 'pending', status_reason = NULL WHERE id = ?",
  )
    .bind(id)
    .run();

  await enqueueJob(c.env, { type: "rag_reindex", packageId: id });

  return c.json({ ok: true, status: "pending" });
});

/** Chamado pelo consumer da fila `vectora-jobs` (job `rag_reindex`). */
export async function processRagReindex(
  env: Env,
  packageId: string,
): Promise<void> {
  await env.DB.prepare(
    "UPDATE rag_packages SET status = 'failed', status_reason = ? WHERE id = ?",
  )
    .bind(NO_STORAGE_PROVIDER_REASON, packageId)
    .run();
}

async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
