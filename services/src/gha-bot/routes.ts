/** gha-bot/ — Vectora Bot for GHA: painel (sessão) + Action pública (token). */
import { Hono } from "hono";
import type { Env } from "../gateway/types";
import { requireUserId } from "../auth/routes";
import { bearerToken, sha256Hex } from "../auth/session";
import { decryptProviderKey, encryptProviderKey } from "./crypto";
import { checkGatewayHealth, dispatchReviewJob } from "../gateway";
import { timingSafeEqual } from "../gateway/auth";

export const ghaBot = new Hono<{ Bindings: Env }>();

const VALID_REVIEW_STYLES = new Set(["strict", "balanced", "lenient"]);

/** Resolve o user_id de um VECTORA_BOT_TOKEN válido (não revogado) — mesmo
 * padrão de `resolveSession`, mas contra `gha_bot_tokens` em vez de
 * `sessions`. Usado só por `GET /gha-bot/config` (a Action, não o painel). */
async function resolveGhaBotToken(
  db: D1Database,
  rawToken: string | null,
): Promise<string | null> {
  if (!rawToken) return null;
  const tokenHash = await sha256Hex(rawToken);
  const row = await db
    .prepare(
      "SELECT user_id, revoked_at FROM gha_bot_tokens WHERE token_hash = ?",
    )
    .bind(tokenHash)
    .first<{ user_id: string; revoked_at: string | null }>();

  if (!row || row.revoked_at) return null;
  return row.user_id;
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

  await c.env.DB.prepare(
    "INSERT INTO gha_bot_tokens (id, user_id, token_hash, repo_scope) VALUES (?, ?, ?, ?)",
  )
    .bind(crypto.randomUUID(), userId, hash, body.repo_scope ?? null)
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

  if (!body.provider || !body.model || !body.provider_api_key) {
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
  const userId = await resolveGhaBotToken(c.env.DB, bearerToken(c.req.raw));
  if (!userId) return c.json({ error: "unauthorized" }, 401);

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

  // Modo self-hosted só é oferecido se o usuário optou E o túnel do
  // gateway está de fato conectado agora — comportamento default (runner
  // efêmero) continua intacto pra quem não optou, e cai pra ele
  // automaticamente se a instância do usuário estiver offline no momento
  // exato desta checagem (sem retry aqui — a Action já roda de novo no
  // próximo PR).
  if (row.self_hosted_enabled === 1) {
    const tokenRow = await c.env.DB.prepare(
      "SELECT token FROM tokens WHERE user_id = ?",
    )
      .bind(userId)
      .first<{ token: string | null }>();
    if (tokenRow?.token) {
      const health = await checkGatewayHealth(c.env, tokenRow.token);
      if (health.connected) {
        return c.json({
          mode: "self-hosted",
          // services.vectora.company (não APP_URL — esse é o site da
          // company, um deploy diferente; mesma convenção hardcoded já
          // usada em rag-library/routes.ts pra auto-referenciar este Worker).
          job_endpoint: "https://services.vectora.company/gha-bot/review",
        });
      }
    }
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
}

/**
 * Cria um job de revisão self-hosted — a Action chama isto (autenticada
 * pelo MESMO VECTORA_BOT_TOKEN de /config, não pela sessão do painel) só
 * depois de /config já ter devolvido mode="self-hosted". Responde 202 com
 * o job_id imediatamente (fire-and-forget pelo túnel — sidesteps o teto
 * de 30s do GatewaySession, pensado pra request/response síncrono).
 */
ghaBot.post("/review", async (c) => {
  const userId = await resolveGhaBotToken(c.env.DB, bearerToken(c.req.raw));
  if (!userId) return c.json({ error: "unauthorized" }, 401);

  const body = await c.req
    .json<{ diff?: string; metadata?: Record<string, string> }>()
    .catch(() => null);
  if (!body?.diff) return c.json({ error: "missing_diff" }, 400);

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
  const callbackSecretHash = await sha256Hex(callbackSecret);
  await c.env.DB.prepare(
    "INSERT INTO gha_bot_review_jobs (id, user_id, callback_secret, callback_secret_hash, status) VALUES (?, ?, '', ?, 'pending')",
  )
    .bind(jobId, userId, callbackSecretHash)
    .run();

  const { delivered } = await dispatchReviewJob(c.env, tokenRow.token, {
    job_id: jobId,
    diff: body.diff,
    metadata: body.metadata ?? {},
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
  const userId = await resolveGhaBotToken(c.env.DB, bearerToken(c.req.raw));
  if (!userId) return c.json({ error: "unauthorized" }, 401);

  const id = c.req.param("id");
  const row = await c.env.DB.prepare(
    "SELECT id, status, review_text, error FROM gha_bot_review_jobs WHERE id = ? AND user_id = ?",
  )
    .bind(id, userId)
    .first<ReviewJobRow>();

  if (!row) return c.json({ error: "not_found" }, 404);
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
