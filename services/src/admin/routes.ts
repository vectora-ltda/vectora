/**
 * admin/ — painel do criador do Vectora: lista de usuários, cupons
 * (desconto/free_lifetime) e presentes de licença. Toda rota exige
 * `role = 'admin'` via requireAdmin — não há UI pública, é whitelist
 * implícita (usuário comum recebe 403, rota não é escondida por segurança
 * por obscuridade).
 */
import { Hono } from "hono";
import { z } from "zod";
import type { Env } from "../gateway/types";
import { requireAdmin } from "../auth/roles";
import { grantSubscription } from "../billing/routes";
import { giftReceivedHtml, issueResponseHtml } from "../lib/email";
import { enqueueEmail } from "../lib/queue";
import { promoteIssue } from "../issues/promotion";

export const admin = new Hono<{ Bindings: Env }>();

// max_redemptions/duration_months chegavam como `unknown` de
// c.req.json<{...}>() com só um cast de tipo TypeScript, sem checagem em
// runtime (achado da auditoria de segurança de 2026-08-30) — um valor não
// numérico bindava direto no D1 sem erro visível.
const CreateCouponSchema = z.object({
  code: z.string().trim().min(3),
  kind: z.enum(["discount", "free_lifetime"]),
  grant_plan_id: z.string().optional(),
  charge_plan_id: z.string().optional(),
  max_redemptions: z.number().int().positive().optional(),
  expires_at: z.string().optional(),
});

const CreateGiftSchema = z.object({
  email: z.string().email(),
  duration_months: z.number().int().positive().optional(),
});

admin.get("/users", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);

  const limit = Math.min(Number(c.req.query("limit") ?? "50"), 200);
  const offset = Number(c.req.query("offset") ?? "0");

  const { results } = await c.env.DB.prepare(
    `SELECT u.id, u.email, u.full_name, u.created_at,
            s.tier, s.status, s.current_period_end
     FROM users u
     LEFT JOIN subscriptions s ON s.user_id = u.id
     ORDER BY u.created_at DESC
     LIMIT ? OFFSET ?`,
  )
    .bind(limit, offset)
    .all();

  return c.json({ users: results });
});

interface CouponListRow {
  id: string;
  code: string;
  kind: string;
  grant_plan_id: string | null;
  charge_plan_id: string | null;
  max_redemptions: number | null;
  times_redeemed: number;
  active: number;
  expires_at: string | null;
  created_at: string;
}

admin.get("/coupons", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);

  const { results } = await c.env.DB.prepare(
    "SELECT * FROM coupons ORDER BY created_at DESC",
  ).all<CouponListRow>();

  return c.json({ coupons: results });
});

admin.post("/coupons", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);

  const parsed = CreateCouponSchema.safeParse(await c.req.json());
  if (!parsed.success) {
    const field = parsed.error.issues[0]?.path[0];
    return c.json({ error: `invalid_${field ? String(field) : "body"}` }, 400);
  }
  const body = parsed.data;

  if (
    body.kind === "discount" &&
    (!body.grant_plan_id || !body.charge_plan_id)
  ) {
    return c.json({ error: "discount_requires_plans" }, 400);
  }

  const code = body.code.trim().toUpperCase();

  try {
    await c.env.DB.prepare(
      `INSERT INTO coupons
         (id, code, kind, grant_plan_id, charge_plan_id, max_redemptions, created_by, expires_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    )
      .bind(
        crypto.randomUUID(),
        code,
        body.kind,
        body.kind === "discount" ? (body.grant_plan_id ?? null) : null,
        body.kind === "discount" ? (body.charge_plan_id ?? null) : null,
        body.max_redemptions ?? null,
        adminId,
        body.expires_at ?? null,
      )
      .run();
  } catch {
    return c.json({ error: "code_taken" }, 409);
  }

  return c.json({ ok: true, code });
});

admin.post("/coupons/:id/deactivate", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);

  const id = c.req.param("id");
  const result = await c.env.DB.prepare(
    "UPDATE coupons SET active = 0 WHERE id = ?",
  )
    .bind(id)
    .run();

  if (result.meta.changes === 0) return c.json({ error: "not_found" }, 404);
  return c.json({ ok: true });
});

admin.get("/gifts", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);

  const { results } = await c.env.DB.prepare(
    `SELECT g.id, g.email, g.duration_months, g.status, g.created_at, g.claimed_at,
            u.email as granted_by_email
     FROM gifts g
     JOIN users u ON u.id = g.granted_by
     ORDER BY g.created_at DESC`,
  ).all();

  return c.json({ gifts: results });
});

admin.post("/gifts", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);

  const parsed = CreateGiftSchema.safeParse(await c.req.json());
  if (!parsed.success) return c.json({ error: "invalid_email" }, 400);
  const email = parsed.data.email.toLowerCase();
  const durationMonths = parsed.data.duration_months ?? null;

  const granter = await c.env.DB.prepare(
    "SELECT full_name FROM users WHERE id = ?",
  )
    .bind(adminId)
    .first<{ full_name: string }>();

  const existingUser = await c.env.DB.prepare(
    "SELECT id FROM users WHERE email = ?",
  )
    .bind(email)
    .first<{ id: string }>();

  const giftId = crypto.randomUUID();
  await c.env.DB.prepare(
    `INSERT INTO gifts (id, email, granted_by, duration_months, status, claimed_user_id, claimed_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(
      giftId,
      email,
      adminId,
      durationMonths,
      existingUser ? "claimed" : "pending",
      existingUser?.id ?? null,
      existingUser ? new Date().toISOString() : null,
    )
    .run();

  if (existingUser) {
    const currentPeriodEnd = durationMonths
      ? (() => {
          const d = new Date();
          d.setMonth(d.getMonth() + durationMonths);
          return d.toISOString();
        })()
      : null;
    await grantSubscription(c.env.DB, existingUser.id, {
      provider: "gift",
      currentPeriodEnd,
    });
  }

  const durationLabel = durationMonths
    ? `${durationMonths} meses`
    : "Vitalício";
  const ctaUrl = existingUser
    ? `${c.env.APP_URL}/dashboard`
    : `${c.env.APP_URL}/auth/signup?email=${encodeURIComponent(email)}`;
  await enqueueEmail(c.env, {
    to: email,
    subject: "Você recebeu o Vectora Pro de presente!",
    html: giftReceivedHtml(
      granter?.full_name ?? "Vectora",
      durationLabel,
      ctaUrl,
    ),
  });

  return c.json({ ok: true, gift_id: giftId, claimed: Boolean(existingUser) });
});

interface AdminIssueRow {
  id: string;
  title: string;
  category: string;
  description: string | null;
  email: string | null;
  files: string | null;
  status: string;
  response: string | null;
  responded_at: string | null;
  archived_at: string | null;
  created_at: string;
  github_repo: string | null;
  github_number: number | null;
  github_url: string | null;
  github_sync_state: string;
  github_sync_error: string | null;
  core_repo: string | null;
  core_number: number | null;
  core_url: string | null;
  approved_at: string | null;
  approved_by: string | null;
}

// Lista completa (com email — o público NUNCA vê esse campo) pro admin
// triar/responder issues. Badge de contagem no company conta status='open'.
// Arquivadas somem daqui por padrão (mesma regra da listagem pública) — pra
// ver/desarquivar uma arquivada, o admin acessa direto por id.
admin.get("/issues", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);

  const limit = Math.min(Number(c.req.query("limit") ?? "50"), 200);
  const offset = Number(c.req.query("offset") ?? "0");

  const { results } = await c.env.DB.prepare(
    "SELECT id, title, category, description, email, files, status, response, responded_at, archived_at, created_at, github_repo, github_number, github_url, github_sync_state, github_sync_error, core_repo, core_number, core_url, approved_at, approved_by FROM issues WHERE archived_at IS NULL ORDER BY created_at DESC LIMIT ? OFFSET ?",
  )
    .bind(limit, offset)
    .all<AdminIssueRow>();

  return c.json({
    issues: results.map((row) => ({
      ...row,
      files: row.files ? (JSON.parse(row.files) as string[]) : [],
    })),
  });
});

// Sempre retorna, mesmo arquivada — é o único jeito do admin ver/desarquivar
// uma issue depois que ela sumiu da listagem.
admin.get("/issues/:id", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);

  const row = await c.env.DB.prepare(
    "SELECT id, title, category, description, email, files, status, response, responded_at, archived_at, created_at, github_repo, github_number, github_url, github_sync_state, github_sync_error, core_repo, core_number, core_url, approved_at, approved_by FROM issues WHERE id = ?",
  )
    .bind(c.req.param("id"))
    .first<AdminIssueRow>();
  if (!row) return c.json({ error: "not_found" }, 404);

  const { results: comments } = await c.env.DB.prepare(
    "SELECT author, body, html_url, created_at FROM issue_comments WHERE issue_id = ? ORDER BY created_at ASC",
  )
    .bind(c.req.param("id"))
    .all();

  return c.json({
    ...row,
    files: row.files ? (JSON.parse(row.files) as string[]) : [],
    comments,
  });
});

admin.post("/issues/:id/approve", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);

  const id = c.req.param("id");
  try {
    const result = await promoteIssue(c.env, id, adminId);
    return c.json({
      ok: true,
      promoted: !result.alreadyPromoted,
      already_promoted: result.alreadyPromoted ?? false,
      url: result.url,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "promotion_failed";
    if (message === "issue_not_found")
      return c.json({ error: "not_found" }, 404);
    if (message === "github_not_configured")
      return c.json({ error: message }, 503);
    console.error("issue_github_promotion_failed", { id, message });
    return c.json({ error: "promotion_failed" }, 502);
  }
});

admin.post("/issues/:id/archive", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);

  const id = c.req.param("id");
  const body = await c.req.json<{ archived?: boolean }>();

  const existing = await c.env.DB.prepare("SELECT id FROM issues WHERE id = ?")
    .bind(id)
    .first<{ id: string }>();
  if (!existing) return c.json({ error: "not_found" }, 404);

  await c.env.DB.prepare("UPDATE issues SET archived_at = ? WHERE id = ?")
    .bind(body.archived ? new Date().toISOString() : null, id)
    .run();

  return c.json({ ok: true });
});

admin.post("/issues/:id/respond", async (c) => {
  const adminId = await requireAdmin(c);
  if (!adminId) return c.json({ error: "forbidden" }, 403);

  const id = c.req.param("id");
  const body = await c.req.json<{ response?: string; resolve?: boolean }>();
  if (!body.response || body.response.trim().length < 3) {
    return c.json({ error: "invalid_response" }, 400);
  }

  const issue = await c.env.DB.prepare(
    "SELECT title, email FROM issues WHERE id = ?",
  )
    .bind(id)
    .first<{ title: string; email: string | null }>();
  if (!issue) return c.json({ error: "not_found" }, 404);

  const newStatus = body.resolve ? "resolved" : "open";
  await c.env.DB.prepare(
    "UPDATE issues SET response = ?, responded_at = datetime('now'), status = ? WHERE id = ?",
  )
    .bind(body.response, newStatus, id)
    .run();

  // Só notifica se o reporter deixou email (opcional no formulário) — sem
  // email, a resposta fica só visível na página pública da issue.
  if (issue.email) {
    await enqueueEmail(c.env, {
      to: issue.email,
      subject: `Resposta à sua issue: ${issue.title}`,
      html: issueResponseHtml(issue.title, body.response),
    });
  }

  return c.json({ ok: true });
});
