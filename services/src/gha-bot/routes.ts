/** gha-bot/ — Vectora Bot for GHA: painel (sessão) + Action pública (token). */
import { Hono } from "hono";
import type { Env } from "../gateway/types";
import { requireUserId } from "../auth/routes";
import { bearerToken, sha256Hex } from "../auth/session";
import { decryptProviderKey, encryptProviderKey } from "./crypto";
import { dispatchReviewJob } from "../gateway";
import { timingSafeEqual } from "../gateway/auth";

export const ghaBot = new Hono<{ Bindings: Env }>();

const VALID_REVIEW_STYLES = new Set(["strict", "balanced", "lenient"]);
// O runner público do GitHub não consegue alcançar um Ollama local. Manter
// o provider fora do contrato evita salvar uma configuração que só falha no
// fallback hosted quando o túnel self-hosted estiver offline.
const VALID_GHA_PROVIDERS = new Set([
  "anthropic",
  "openai",
  "google_genai",
  "openrouter",
]);

/** Limites compartilhados com o cliente Python do gateway. */
export const MAX_REVIEW_DIFF_BYTES = 6_000_000;
export const MAX_REVIEW_JOB_BYTES = 8_000_000;
const MAX_REVIEW_METADATA_ENTRIES = 64;
const MAX_REVIEW_METADATA_VALUE_LENGTH = 2_000;

type ReviewInput = { diff: string; metadata: Record<string, string> };

interface GhaBotTokenIdentity {
  userId: string;
  repoScope: string | null;
}

function parseReviewInput(
  value: unknown,
): { ok: true; input: ReviewInput } | { ok: false; error: string } {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { ok: false, error: "invalid_payload" };
  }
  const body = value as { diff?: unknown; metadata?: unknown };
  if (typeof body.diff !== "string" || body.diff.length === 0) {
    return { ok: false, error: "missing_diff" };
  }
  if (new TextEncoder().encode(body.diff).byteLength > MAX_REVIEW_DIFF_BYTES) {
    return { ok: false, error: "diff_too_large" };
  }
  const metadata = body.metadata ?? {};
  if (
    typeof metadata !== "object" ||
    metadata === null ||
    Array.isArray(metadata)
  ) {
    return { ok: false, error: "invalid_metadata" };
  }
  const entries = Object.entries(metadata);
  if (entries.length > MAX_REVIEW_METADATA_ENTRIES) {
    return { ok: false, error: "metadata_too_large" };
  }
  const normalized: Record<string, string> = {};
  for (const [key, item] of entries) {
    if (
      key.length === 0 ||
      key.length > 200 ||
      typeof item !== "string" ||
      item.length > MAX_REVIEW_METADATA_VALUE_LENGTH
    ) {
      return { ok: false, error: "invalid_metadata" };
    }
    normalized[key] = item;
  }
  return { ok: true, input: { diff: body.diff, metadata: normalized } };
}

/** Resolve o dono e o escopo de um VECTORA_BOT_TOKEN válido (não revogado) — mesmo
 * padrão de `resolveSession`, mas contra `gha_bot_tokens` em vez de
 * `sessions`. Usado só por `GET /gha-bot/config` (a Action, não o painel). */
async function resolveGhaBotToken(
  db: D1Database,
  rawToken: string | null,
): Promise<GhaBotTokenIdentity | null> {
  if (!rawToken) return null;
  const tokenHash = await sha256Hex(rawToken);
  const row = await db
    .prepare(
      "SELECT user_id, repo_scope, revoked_at FROM gha_bot_tokens WHERE token_hash = ?",
    )
    .bind(tokenHash)
    .first<{
      user_id: string;
      repo_scope: string | null;
      revoked_at: string | null;
    }>();

  if (!row || row.revoked_at) return null;
  return { userId: row.user_id, repoScope: row.repo_scope };
}

function requestedRepository(query: Record<string, string>): string | null {
  return (query.repository ?? query.repo ?? "").trim() || null;
}

function tokenAllowsRepository(
  identity: GhaBotTokenIdentity,
  repository: string | null,
):
  | { ok: true }
  | { ok: false; error: "repo_scope_required" | "repo_scope_forbidden" } {
  if (!identity.repoScope) return { ok: true };
  if (!repository) return { ok: false, error: "repo_scope_required" };
  return identity.repoScope === repository
    ? { ok: true }
    : { ok: false, error: "repo_scope_forbidden" };
}

async function isProUser(db: D1Database, userId: string): Promise<boolean> {
  const row = await db
    .prepare(
      "SELECT 1 FROM subscriptions WHERE user_id = ? AND tier = 'pro' AND status = 'active'",
    )
    .bind(userId)
    .first();
  return row !== null;
}

ghaBot.get("/tokens", async (c) => {
  const userId = await requireUserId(c);
  if (!userId) return c.json({ error: "unauthorized" }, 401);

  const { results } = await c.env.DB.prepare(
    "SELECT id, repo_scope, created_at, revoked_at FROM gha_bot_tokens WHERE user_id = ? ORDER BY created_at DESC",
  )
    .bind(userId)
    .all<{
      id: string;
      repo_scope: string | null;
      created_at: string;
      revoked_at: string | null;
    }>();

  return c.json(results);
});

ghaBot.post("/tokens", async (c) => {
  const userId = await requireUserId(c);
  if (!userId) return c.json({ error: "unauthorized" }, 401);

  const body = await c.req
    .json<{ repo_scope?: string }>()
    .catch(() => ({}) as { repo_scope?: string });

  const raw = crypto.randomUUID();
  const hash = await sha256Hex(raw);
  const repoScope =
    typeof body.repo_scope === "string" ? body.repo_scope.trim() || null : null;

  await c.env.DB.prepare(
    "INSERT INTO gha_bot_tokens (id, user_id, token_hash, repo_scope) VALUES (?, ?, ?, ?)",
  )
    .bind(crypto.randomUUID(), userId, hash, repoScope)
    .run();

  // Mostrado uma vez só — só o hash fica salvo, igual api_keys.
  return c.json({ secret: raw });
});

ghaBot.post("/tokens/:id/revoke", async (c) => {
  const userId = await requireUserId(c);
  if (!userId) return c.json({ error: "unauthorized" }, 401);

  const id = c.req.param("id");
  const result = await c.env.DB.prepare(
    "UPDATE gha_bot_tokens SET revoked_at = datetime('now') WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
  )
    .bind(id, userId)
    .run();

  if (result.meta.changes === 0) return c.json({ error: "not_found" }, 404);
  return c.json({ ok: true });
});

ghaBot.get("/settings", async (c) => {
  const userId = await requireUserId(c);
  if (!userId) return c.json({ error: "unauthorized" }, 401);

  const row = await c.env.DB.prepare(
    "SELECT provider, model, review_style, self_hosted_enabled, updated_at FROM gha_bot_config WHERE user_id = ?",
  )
    .bind(userId)
    .first<{
      provider: string;
      model: string;
      review_style: string;
      self_hosted_enabled: number;
      updated_at: string;
    }>();

  // Chave de provider NUNCA volta pro painel — só o nome da secret_ref é
  // gerenciado, o valor em si só é lido em GET /gha-bot/config (pela Action,
  // via token, não pela sessão do painel).
  if (!row) return c.json(null);
  return c.json({ ...row, self_hosted_enabled: row.self_hosted_enabled === 1 });
});

ghaBot.put("/settings", async (c) => {
  const userId = await requireUserId(c);
  if (!userId) return c.json({ error: "unauthorized" }, 401);

  const body = await c.req.json<{
    provider?: string;
    model?: string;
    provider_api_key?: string;
    review_style?: string;
    // Toggle de "usar minha própria instância Vectora" — GET /config só
    // oferece o modo self-hosted de verdade se, ALÉM disto, o túnel do
    // gateway estiver conectado no momento (ver checkGatewayHealth); os
    // campos provider/model/api_key continuam obrigatórios mesmo com
    // self_hosted_enabled=true — servem de fallback automático se a
    // instância do usuário cair.
    self_hosted_enabled?: boolean;
  }>();

  if (
    !body.provider ||
    !VALID_GHA_PROVIDERS.has(body.provider) ||
    !body.model ||
    !body.provider_api_key
  ) {
    return c.json({ error: "missing_fields" }, 400);
  }
  const reviewStyle = body.review_style ?? "balanced";
  if (!VALID_REVIEW_STYLES.has(reviewStyle)) {
    return c.json({ error: "invalid_review_style" }, 400);
  }

  const encrypted = await encryptProviderKey(
    c.env.GHA_BOT_ENCRYPTION_KEY,
    body.provider_api_key,
  );
  const selfHosted = body.self_hosted_enabled ? 1 : 0;

  await c.env.DB.prepare(
    `INSERT INTO gha_bot_config (user_id, provider, model, provider_api_key_encrypted, review_style, self_hosted_enabled, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
     ON CONFLICT (user_id) DO UPDATE SET
       provider = excluded.provider,
       model = excluded.model,
       provider_api_key_encrypted = excluded.provider_api_key_encrypted,
       review_style = excluded.review_style,
       self_hosted_enabled = excluded.self_hosted_enabled,
       updated_at = datetime('now')`,
  )
    .bind(userId, body.provider, body.model, encrypted, reviewStyle, selfHosted)
    .run();

  return c.json({ ok: true });
});

// Sem auth — mesma justificativa de updates/worker.ts::/download/:channel/
// :target: é só um binário público, não dado sensível. A Action busca a
// versão mais recente publicada (não recebe número de versão do usuário).
ghaBot.get("/download/latest", async (c) => {
  const list = await c.env.R2.list({ prefix: "gha-bot/" });
  const versions = new Set<string>();
  for (const obj of list.objects) {
    const match = /^gha-bot\/([^/]+)\//.exec(obj.key);
    if (match?.[1]) versions.add(match[1]);
  }
  if (versions.size === 0) return c.json({ error: "not_found" }, 404);
  // Versões seguem semver (x.y.z) — ordenação lexicográfica não basta
  // (ex. "0.1.9" > "0.1.10" lexicograficamente), comparar por partes numéricas.
  const latest = [...versions].sort((a, b) => {
    const pa = a.split(".").map(Number);
    const pb = b.split(".").map(Number);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
      const diff = (pa[i] ?? 0) - (pb[i] ?? 0);
      if (diff !== 0) return diff;
    }
    return 0;
  })[versions.size - 1] as string;

  const obj = await c.env.R2.get(
    `gha-bot/${latest}/vectora-cli-linux-x64.tar.gz`,
  );
  if (!obj) return c.json({ error: "not_found" }, 404);
  return new Response(obj.body, {
    headers: {
      "Content-Type": "application/gzip",
      "Content-Disposition":
        'attachment; filename="vectora-cli-linux-x64.tar.gz"',
      "Cache-Control": "public, max-age=300",
      "X-Vectora-Version": latest,
    },
  });
});

ghaBot.get("/config", async (c) => {
  const identity = await resolveGhaBotToken(c.env.DB, bearerToken(c.req.raw));
  if (!identity) return c.json({ error: "unauthorized" }, 401);

  const scope = tokenAllowsRepository(
    identity,
    requestedRepository(c.req.query()),
  );
  if (!scope.ok) return c.json({ error: scope.error }, 403);
  const userId = identity.userId;

  if (!(await isProUser(c.env.DB, userId))) {
    return c.json({ error: "pro_required" }, 403);
  }

  const row = await c.env.DB.prepare(
    "SELECT provider, model, provider_api_key_encrypted, review_style, self_hosted_enabled FROM gha_bot_config WHERE user_id = ?",
  )
    .bind(userId)
    .first<{
      provider: string;
      model: string;
      provider_api_key_encrypted: string;
      review_style: string;
      self_hosted_enabled: number;
    }>();

  if (!row) return c.json({ error: "not_configured" }, 404);
  if (!VALID_GHA_PROVIDERS.has(row.provider)) {
    return c.json({ error: "reconfigure_required" }, 409);
  }

  const apiKey = await decryptProviderKey(
    c.env.GHA_BOT_ENCRYPTION_KEY,
    row.provider_api_key_encrypted,
  );

  return c.json({
    mode: "hosted",
    provider: row.provider,
    model: row.model,
    api_key: apiKey,
    review_style: row.review_style,
  });
});

interface ReviewJobRow {
  id: string;
  status: "pending" | "done" | "failed";
  review_text: string | null;
  error: string | null;
  repository: string | null;
}

/**
 * Cria um job de revisão self-hosted — a Action chama isto (autenticada
 * pelo MESMO VECTORA_BOT_TOKEN de /config, não pela sessão do painel) só
 * depois de /config já ter devolvido mode="self-hosted". Responde 202 com
 * o job_id imediatamente (fire-and-forget pelo túnel — sidesteps o teto
 * de 30s do GatewaySession, pensado pra request/response síncrono).
 */
ghaBot.post("/review", async (c) => {
  const identity = await resolveGhaBotToken(c.env.DB, bearerToken(c.req.raw));
  if (!identity) return c.json({ error: "unauthorized" }, 401);

  const userId = identity.userId;
  if (!(await isProUser(c.env.DB, userId))) {
    return c.json({ error: "pro_required" }, 403);
  }
  const settings = await c.env.DB.prepare(
    "SELECT self_hosted_enabled FROM gha_bot_config WHERE user_id = ?",
  )
    .bind(userId)
    .first<{ self_hosted_enabled: number }>();
  if (!settings || settings.self_hosted_enabled !== 1) {
    return c.json({ error: "self_hosted_not_enabled" }, 403);
  }

  const body = await c.req.json<unknown>().catch(() => null);
  const parsed = parseReviewInput(body);
  if (!parsed.ok) return c.json({ error: parsed.error }, 400);

  const metadataRepository =
    parsed.input.metadata.repository ??
    parsed.input.metadata.repo ??
    parsed.input.metadata.repo_full_name ??
    null;
  const scope = tokenAllowsRepository(identity, metadataRepository);
  if (!scope.ok) return c.json({ error: scope.error }, 403);

  const tokenRow = await c.env.DB.prepare(
    "SELECT token FROM tokens WHERE user_id = ?",
  )
    .bind(userId)
    .first<{ token: string | null }>();
  if (!tokenRow?.token) {
    return c.json({ error: "gateway_not_registered" }, 409);
  }

  const jobId = crypto.randomUUID();
  const callbackSecret = crypto.randomUUID();
  const reviewPayload = {
    type: "review_job" as const,
    job_id: jobId,
    diff: parsed.input.diff,
    metadata: parsed.input.metadata,
    callback_secret: callbackSecret,
  };
  if (
    new TextEncoder().encode(JSON.stringify(reviewPayload)).byteLength >
    MAX_REVIEW_JOB_BYTES
  ) {
    return c.json({ error: "review_payload_too_large" }, 400);
  }
  const callbackSecretHash = await sha256Hex(callbackSecret);
  await c.env.DB.prepare(
    "INSERT INTO gha_bot_review_jobs (id, user_id, repository, callback_secret, callback_secret_hash, status) VALUES (?, ?, ?, '', ?, 'pending')",
  )
    .bind(jobId, userId, metadataRepository, callbackSecretHash)
    .run();

  const { delivered } = await dispatchReviewJob(c.env, tokenRow.token, {
    job_id: jobId,
    diff: parsed.input.diff,
    metadata: parsed.input.metadata,
    callback_secret: callbackSecret,
  });

  if (!delivered) {
    await c.env.DB.prepare(
      "UPDATE gha_bot_review_jobs SET status = 'failed', error = ?, updated_at = datetime('now') WHERE id = ?",
    )
      .bind("instância Vectora desconectou antes do job ser entregue", jobId)
      .run();
    return c.json({ error: "not_delivered", job_id: jobId }, 502);
  }

  return c.json({ job_id: jobId }, 202);
});

/** A Action faz long-poll aqui até status != "pending". */
ghaBot.get("/review/:id", async (c) => {
  const identity = await resolveGhaBotToken(c.env.DB, bearerToken(c.req.raw));
  if (!identity) return c.json({ error: "unauthorized" }, 401);
  const userId = identity.userId;

  const id = c.req.param("id");
  const row = await c.env.DB.prepare(
    "SELECT id, status, review_text, error, repository FROM gha_bot_review_jobs WHERE id = ? AND user_id = ?",
  )
    .bind(id, userId)
    .first<ReviewJobRow>();

  if (!row) return c.json({ error: "not_found" }, 404);
  const scope = tokenAllowsRepository(identity, row.repository);
  if (!scope.ok) return c.json({ error: scope.error }, 403);
  return c.json(row);

});

/**
 * O backend Python do usuário chama isto quando termina a revisão — FORA
 * do túnel (POST outbound normal, sem problema de NAT/firewall). Autenticado
 * por `callback_secret`, não pelo VECTORA_BOT_TOKEN de /config (o backend
 * Python que roda o job não tem esse token disponível — é gerado só pra
 * Action, no painel). O secret é por-job, gerado no INSERT de POST /review e
 * entregue só dentro do payload `review_job` pelo túnel — nunca na resposta
 * HTTP da Action, que aparece em log de workflow. `job_id` sozinho (esse
 * sim, visível em log) não bastaria: sem o secret, qualquer um que soubesse
 * o id escreveria review_text arbitrário no PR antes do backend legítimo.
 * `AND status = 'pending'` mantém o update de uso único.
 */
ghaBot.post("/review/:id/result", async (c) => {
  const id = c.req.param("id");
  const secret = bearerToken(c.req.raw);
  if (!secret) return c.json({ error: "unauthorized" }, 401);

  const body = await c.req
    .json<{ review_text?: string; error?: string }>()
    .catch(() => null);
  if (!body || (!body.review_text && !body.error)) {
    return c.json({ error: "missing_fields" }, 400);
  }

  const row = await c.env.DB.prepare(
    "SELECT callback_secret_hash, callback_secret FROM gha_bot_review_jobs WHERE id = ? AND status = 'pending'",
  )
    .bind(id)
    .first<{
      callback_secret_hash: string | null;
      callback_secret: string | null;
    }>();
  const secretHash = await sha256Hex(secret);
  const hashMatches =
    row?.callback_secret_hash !== null &&
    row?.callback_secret_hash !== undefined &&
    timingSafeEqual(secretHash, row.callback_secret_hash);
  const legacyMatches =
    !hashMatches &&
    row?.callback_secret !== null &&
    row?.callback_secret !== undefined &&
    timingSafeEqual(secret, row.callback_secret);
  if (!row || (!hashMatches && !legacyMatches)) {
    return c.json({ error: "not_found" }, 404);
  }

  const status = body.error ? "failed" : "done";
  const result = await c.env.DB.prepare(
    `UPDATE gha_bot_review_jobs
     SET status = ?, review_text = ?, error = ?, callback_secret = '',
         callback_secret_hash = ?, updated_at = datetime('now')
     WHERE id = ? AND status = 'pending'
       AND (callback_secret_hash = ? OR (callback_secret_hash IS NULL AND callback_secret = ?))`,
  )
    .bind(
      status,
      body.review_text ?? null,
      body.error ?? null,
      secretHash,
      id,
      secretHash,
      secret,
    )
    .run();

  if (result.meta.changes === 0) return c.json({ error: "not_found" }, 404);
  return c.json({ ok: true });
});
