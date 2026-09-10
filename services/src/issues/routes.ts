/** issues/ — porta company/src/server/fns/issues.ts (issues + waitlist, públicos). */
import { Hono } from "hono";
import type { Env } from "../gateway/types";
import { verifyTurnstile } from "../lib/turnstile";
import { SUPPORT_EMAIL, waitlistJoinedHtml } from "../lib/email";
import { enqueueEmail } from "../lib/queue";
import {
  addComment,
  createIssue,
  findCommentByMarker,
  findIssueByMarker,
  intakeRepo,
  listComments,
  type GitHubIssue,
  updateIssue,
} from "./github";
import { githubApprovalAllowed, promoteIssue } from "./promotion";

export const issues = new Hono<{ Bindings: Env }>();

const CATEGORIES = new Set(["bug", "feedback", "feature"]);

export const MAX_ISSUE_FILES = 3;

// Tipos aceitos como anexo e o teto de bytes de cada um. Vídeo tem teto
// maior porque 30s de tela em mp4/webm passam fácil de 5 MiB.
export const ISSUE_FILE_LIMITS: Record<string, number> = {
  "image/png": 5 * 1024 * 1024,
  "image/jpeg": 5 * 1024 * 1024,
  "image/webp": 5 * 1024 * 1024,
  "video/mp4": 50 * 1024 * 1024,
  "video/webm": 50 * 1024 * 1024,
};

interface IssueFields {
  title?: string;
  category?: string;
  description?: string;
  email?: string;
  turnstileToken?: string;
  files: File[];
}

function githubBody(
  category: string,
  description: string | undefined,
  sourceId: string,
): string {
  return [
    `<!-- vectora-company-issue:${sourceId} -->`,
    `**Categoria:** ${category}`,
    "",
    description?.trim() || "Sem descrição adicional.",
    "",
    `Origem: https://vectora.company/issues/${sourceId}`,
  ].join("\n");
}

async function responseMarker(
  issueId: string,
  response: string,
): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(response),
  );
  const suffix = Array.from(new Uint8Array(digest), (value) =>
    value.toString(16).padStart(2, "0"),
  )
    .join("")
    .slice(0, 24);
  return `vectora-company-response:${issueId}:${suffix}`;
}

/** Publica uma resposta de forma idempotente e conclui a issue pública. */
export async function syncIssueResponse(
  env: Env,
  issueId: string,
  repo: string,
  number: number,
  response: string,
  resolve: boolean,
): Promise<void> {
  const marker = await responseMarker(issueId, response);
  const existing = await findCommentByMarker(env, repo, number, marker);
  if (!existing) {
    await addComment(env, repo, number, `<!-- ${marker} -->\n${response}`);
  }
  if (resolve) {
    await updateIssue(env, repo, number, { state: "closed" });
  }
  await env.DB.prepare(
    "UPDATE issues SET github_sync_state = 'synced', github_sync_error = NULL WHERE id = ?",
  )
    .bind(issueId)
    .run();
}

/** Publica ou reconcilia uma issue da Company no repositório público. */
export async function syncCreatedIssue(
  env: Env,
  issueId: string,
  title: string,
  category: string,
  description: string | undefined,
): Promise<void> {
  if (!env.GITHUB_ISSUES_TOKEN && !env.GITHUB_TOKEN) return;
  const existing = await env.DB.prepare(
    "SELECT github_repo, github_number, github_url, github_sync_state, response, status FROM issues WHERE id = ?",
  )
    .bind(issueId)
    .first<{
      github_repo: string | null;
      github_number: number | null;
      github_url: string | null;
      github_sync_state: string;
      response: string | null;
      status: string;
    }>();
  if (existing?.github_repo && existing.github_number && existing.github_url) {
    try {
      await reconcileIssueComments(
        env,
        issueId,
        existing.github_repo,
        existing.github_number,
      );
      if (
        existing.response &&
        ["response_pending", "response_error"].includes(
          existing.github_sync_state,
        )
      ) {
        await syncIssueResponse(
          env,
          issueId,
          existing.github_repo,
          existing.github_number,
          existing.response,
          existing.status === "resolved",
        );
      }
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "github_sync_failed";
      await env.DB.prepare(
        "UPDATE issues SET github_sync_state = 'response_pending', github_sync_error = ? WHERE id = ?",
      )
        .bind(message.slice(0, 200), issueId)
        .run();
      console.error("issue_github_reconcile_failed", { issueId, message });
    }
    return;
  }
  try {
    const repo = intakeRepo(env);
    const marker = `vectora-company-issue:${issueId}`;
    const body = githubBody(category, description, issueId);
    const claimed = await env.DB.prepare(
      "UPDATE issues SET github_sync_state = 'sync_pending', github_sync_error = NULL WHERE id = ? AND github_number IS NULL AND github_sync_state != 'sync_pending'",
    )
      .bind(issueId)
      .run();
    if (claimed.meta.changes === 0) {
      const current = await env.DB.prepare(
        "SELECT github_repo, github_number, github_url FROM issues WHERE id = ?",
      )
        .bind(issueId)
        .first<{
          github_repo: string | null;
          github_number: number | null;
          github_url: string | null;
        }>();
      if (current?.github_repo && current.github_number && current.github_url) {
        await reconcileIssueComments(
          env,
          issueId,
          current.github_repo,
          current.github_number,
        );
      }
      return;
    }
    const existingRemote = await findIssueByMarker(env, repo, marker);
    const created =
      existingRemote ?? (await createIssue(env, repo, title, body));
    await env.DB.prepare(
      "UPDATE issues SET github_repo = ?, github_number = ?, github_url = ?, github_sync_state = 'synced', github_sync_error = NULL WHERE id = ?",
    )
      .bind(repo, created.number, created.html_url, issueId)
      .run();
    await reconcileIssueComments(env, issueId, repo, created.number);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "github_sync_failed";
    await env.DB.prepare(
      "UPDATE issues SET github_sync_state = 'error', github_sync_error = ? WHERE id = ?",
    )
      .bind(message.slice(0, 200), issueId)
      .run();
    console.error("issue_github_sync_failed", { issueId, message });
  }
}

/** Reconcilia comentários para recuperar entregas de webhook perdidas. */
export async function reconcileIssueComments(
  env: Env,
  issueId: string,
  repo: string,
  number: number,
): Promise<void> {
  const comments = await listComments(env, repo, number);
  const remoteIds = comments.map((comment) => comment.id);
  for (const comment of comments) {
    await env.DB.prepare(
      `INSERT INTO issue_comments (id, issue_id, github_comment_id, author, body, html_url, created_at, updated_at, deleted_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
       ON CONFLICT(issue_id, github_comment_id) DO UPDATE SET author = excluded.author, body = excluded.body, html_url = excluded.html_url, updated_at = excluded.updated_at, deleted_at = NULL`,
    )
      .bind(
        crypto.randomUUID(),
        issueId,
        comment.id,
        comment.user?.login ?? "github-user",
        comment.body,
        comment.html_url,
        comment.created_at,
        comment.updated_at ?? comment.created_at,
      )
      .run();
  }
  if (remoteIds.length > 0) {
    const placeholders = remoteIds.map(() => "?").join(", ");
    await env.DB.prepare(
      `UPDATE issue_comments SET deleted_at = datetime('now') WHERE issue_id = ? AND github_comment_id NOT IN (${placeholders}) AND deleted_at IS NULL`,
    )
      .bind(issueId, ...remoteIds)
      .run();
  } else {
    await env.DB.prepare(
      "UPDATE issue_comments SET deleted_at = datetime('now') WHERE issue_id = ? AND deleted_at IS NULL",
    )
      .bind(issueId)
      .run();
  }
}

/** Retoma respostas públicas persistidas após falhas transitórias do GitHub. */
export async function reconcilePendingIssueResponses(env: Env): Promise<void> {
  const { results } = await env.DB.prepare(
    "SELECT id, response, status, github_repo, github_number FROM issues WHERE github_sync_state = 'response_pending' AND response IS NOT NULL AND github_repo IS NOT NULL AND github_number IS NOT NULL ORDER BY responded_at ASC LIMIT 25",
  ).all<{
    id: string;
    response: string;
    status: string;
    github_repo: string;
    github_number: number;
  }>();
  for (const issue of results) {
    try {
      await syncIssueResponse(
        env,
        issue.id,
        issue.github_repo,
        issue.github_number,
        issue.response,
        issue.status === "resolved",
      );
    } catch (error) {
      console.error("issue_github_response_retry_failed", {
        issueId: issue.id,
        message: error instanceof Error ? error.message : "github_sync_failed",
      });
    }
  }
}

async function readIssueBody(c: {
  req: {
    header: (name: string) => string | undefined;
    json: <T>() => Promise<T>;
    parseBody: (opts: { all: true }) => Promise<Record<string, unknown>>;
  };
}): Promise<IssueFields> {
  const contentType = c.req.header("Content-Type") ?? "";
  if (!contentType.includes("multipart/form-data")) {
    const body = await c.req.json<Omit<IssueFields, "files">>();
    return { ...body, files: [] };
  }
  const body = await c.req.parseBody({ all: true });
  const raw = body.files;
  const files = (Array.isArray(raw) ? raw : raw ? [raw] : []).filter(
    (f): f is File => f instanceof File,
  );
  return {
    title: typeof body.title === "string" ? body.title : undefined,
    category: typeof body.category === "string" ? body.category : undefined,
    description:
      typeof body.description === "string" ? body.description : undefined,
    email: typeof body.email === "string" ? body.email : undefined,
    turnstileToken:
      typeof body.turnstileToken === "string" ? body.turnstileToken : undefined,
    files,
  };
}

issues.post("/", async (c) => {
  const body = await readIssueBody(c);

  if (!body.title || body.title.length < 3 || body.title.length > 200) {
    return c.json({ error: "invalid_title" }, 400);
  }
  if (!body.category || !CATEGORIES.has(body.category)) {
    return c.json({ error: "invalid_category" }, 400);
  }
  if (body.files.length > MAX_ISSUE_FILES) {
    return c.json({ error: "too_many_files" }, 400);
  }
  for (const file of body.files) {
    const limit = ISSUE_FILE_LIMITS[file.type];
    if (limit === undefined) return c.json({ error: "invalid_file_type" }, 400);
    if (file.size > limit) return c.json({ error: "file_too_large" }, 413);
  }
  if (!body.turnstileToken) return c.json({ error: "turnstile_required" }, 400);

  const turnstile = await verifyTurnstile(
    body.turnstileToken,
    c.env.TURNSTILE_SECRET_KEY,
    c.req.header("cf-connecting-ip"),
  );
  if (!turnstile.success) return c.json({ error: "turnstile_failed" }, 400);

  const issueId = crypto.randomUUID();
  const fileKeys: string[] = [];
  for (const file of body.files) {
    // UUID na key torna o caminho não-enumerável — o GET /files é público,
    // mas só quem tem a key (listagem da issue) chega no arquivo.
    const safeName = file.name.replace(/[^\w.-]/g, "_").slice(0, 80);
    const key = `issues/${issueId}/${crypto.randomUUID()}-${safeName}`;
    await c.env.R2.put(key, file.stream(), {
      httpMetadata: { contentType: file.type },
    });
    fileKeys.push(key);
  }

  await c.env.DB.prepare(
    "INSERT INTO issues (id, title, category, description, email, files) VALUES (?, ?, ?, ?, ?, ?)",
  )
    .bind(
      issueId,
      body.title,
      body.category,
      body.description ?? null,
      body.email || null,
      fileKeys.length > 0 ? JSON.stringify(fileKeys) : null,
    )
    .run();

  const syncPromise = syncCreatedIssue(
    c.env,
    issueId,
    body.title,
    body.category,
    body.description,
  );
  try {
    c.executionCtx.waitUntil(syncPromise);
  } catch {
    // O runtime de testes não fornece ExecutionContext; aguarde nesse caso.
    await syncPromise;
  }

  const filesHtml =
    fileKeys.length > 0
      ? `<p><strong>Anexos:</strong> ${fileKeys
          .map(
            (k) =>
              `<a href="https://services.vectora.company/issues/files/${k}">${k}</a>`,
          )
          .join(" · ")}</p>`
      : "";
  await enqueueEmail(c.env, {
    to: SUPPORT_EMAIL,
    subject: `[${body.category.toUpperCase()}] ${body.title}`,
    html: `
      <p><strong>Categoria:</strong> ${body.category}</p>
      <p><strong>Título:</strong> ${body.title}</p>
      <p><strong>Descrição:</strong> ${body.description ?? "—"}</p>
      <p><strong>Email:</strong> ${body.email ?? "—"}</p>
      ${filesHtml}
    `,
  });

  return c.json({ ok: true });
});

// Lista pública das issues abertas. NUNCA seleciona `email` (privacidade do
// reporter). `files` sai como array de keys R2 (servidas em GET /files/*).
issues.get("/", async (c) => {
  const { results } = await c.env.DB.prepare(
    "SELECT id, title, category, description, files, created_at FROM issues WHERE archived_at IS NULL ORDER BY created_at DESC LIMIT 100",
  ).all<{ files: string | null } & Record<string, unknown>>();
  return c.json(
    results.map((row) => ({
      ...row,
      files: row.files ? (JSON.parse(row.files) as string[]) : [],
    })),
  );
});

/**
 * GitHub webhook for the public intake repository.
 * The signature is mandatory when a secret is configured; an unconfigured
 * production endpoint fails closed instead of accepting unsigned events.
 */
issues.post("/github/webhook", async (c) => {
  const secret = c.env.GITHUB_ISSUES_WEBHOOK_SECRET?.trim();
  if (!secret) return c.json({ error: "github_webhook_not_configured" }, 503);

  const body = await c.req.raw.clone().arrayBuffer();
  const deliveryId = c.req.header("x-github-delivery")?.trim();
  if (!deliveryId) return c.json({ error: "delivery_id_required" }, 400);
  const signature = c.req.header("x-hub-signature-256") ?? "";
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const providedHex = signature.startsWith("sha256=") ? signature.slice(7) : "";
  const provided =
    providedHex.length === 64 && /^[0-9a-f]+$/i.test(providedHex)
      ? Uint8Array.from(providedHex.match(/.{2}/g)!, (byte) =>
          parseInt(byte, 16),
        )
      : new Uint8Array();
  const valid = await crypto.subtle.verify("HMAC", key, provided, body);
  if (!valid) {
    return c.json({ error: "invalid_signature" }, 401);
  }

  const attemptToken = crypto.randomUUID();
  const leaseUntil = new Date(Date.now() + 5 * 60 * 1000)
    .toISOString()
    .slice(0, 19)
    .replace("T", " ");
  const delivery = await c.env.DB.prepare(
    `INSERT INTO github_webhook_deliveries
       (delivery_id, state, attempt_token, lease_until)
     VALUES (?, 'processing', ?, ?)
     ON CONFLICT(delivery_id) DO UPDATE SET
       state = 'processing', error = NULL, attempt_token = excluded.attempt_token,
       lease_until = excluded.lease_until, updated_at = datetime('now')
     WHERE github_webhook_deliveries.state = 'failed'
        OR (github_webhook_deliveries.state = 'processing'
            AND (github_webhook_deliveries.lease_until IS NULL
                 OR github_webhook_deliveries.lease_until <= datetime('now')))`,
  )
    .bind(deliveryId, attemptToken, leaseUntil)
    .run();
  if (delivery.meta.changes === 0) return c.json({ ok: true, duplicate: true });

  let payload: {
    action?: string;
    issue?: GitHubIssue & { labels?: Array<{ name?: string }> };
    comment?: {
      id: number;
      body?: string;
      html_url?: string;
      created_at?: string;
      user?: { login?: string };
    };
    repository?: { full_name?: string };
    sender?: { login?: string };
    label?: { name?: string };
  };
  try {
    payload = JSON.parse(new TextDecoder().decode(body)) as typeof payload;
  } catch {
    await c.env.DB.prepare(
      "UPDATE github_webhook_deliveries SET state = 'failed', error = ?, updated_at = datetime('now') WHERE delivery_id = ? AND state = 'processing' AND attempt_token = ?",
    )
      .bind("invalid_json", deliveryId, attemptToken)
      .run();
    return c.json({ error: "invalid_json" }, 400);
  }
  try {
    const repo = payload.repository?.full_name;
    const issue = payload.issue;
    if (repo !== intakeRepo(c.env) || !issue?.number) {
      await c.env.DB.prepare(
        "UPDATE github_webhook_deliveries SET state = 'done', updated_at = datetime('now') WHERE delivery_id = ? AND state = 'processing' AND attempt_token = ?",
      )
        .bind(deliveryId, attemptToken)
        .run();
      return c.json({ ok: true, ignored: true });
    }

    const existing = await c.env.DB.prepare(
      "SELECT id FROM issues WHERE github_repo = ? AND github_number = ?",
    )
      .bind(repo, issue.number)
      .first<{ id: string }>();
    let issueId = existing?.id;
    if (!issueId && payload.action === "opened") {
      issueId = crypto.randomUUID();
      await c.env.DB.prepare(
        "INSERT OR IGNORE INTO issues (id, title, category, description, status, github_repo, github_number, github_url, github_sync_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'synced')",
      )
        .bind(
          issueId,
          issue.title,
          "feedback",
          issue.body ?? null,
          issue.state === "closed" ? "resolved" : "open",
          repo,
          issue.number,
          issue.html_url,
        )
        .run();
    }
    if (!issueId) {
      await c.env.DB.prepare(
        "UPDATE github_webhook_deliveries SET state = 'done', updated_at = datetime('now') WHERE delivery_id = ? AND state = 'processing' AND attempt_token = ?",
      )
        .bind(deliveryId, attemptToken)
        .run();
      return c.json({ ok: true, ignored: true });
    }

    if (
      ["edited", "reopened", "closed", "labeled", "unlabeled"].includes(
        payload.action ?? "",
      )
    ) {
      const approved = issue.labels?.some(
        (label) => label.name === "approved-for-core",
      );
      await c.env.DB.prepare(
        "UPDATE issues SET title = ?, description = ?, status = ?, github_url = ?, github_sync_state = CASE WHEN github_sync_state IN ('promotion_pending', 'promotion_failed', 'approval_error', 'promoted') THEN github_sync_state ELSE 'synced' END, github_sync_error = CASE WHEN github_sync_state IN ('promotion_pending', 'promotion_failed', 'approval_error', 'promoted') THEN github_sync_error ELSE NULL END WHERE id = ?",
      )
        .bind(
          issue.title,
          issue.body ?? null,
          issue.state === "closed" ? "resolved" : "open",
          issue.html_url,
          issueId,
        )
        .run();
      if (
        payload.action === "labeled" &&
        payload.label?.name === "approved-for-core" &&
        approved &&
        payload.sender?.login &&
        githubApprovalAllowed(c.env, payload.sender.login)
      ) {
        try {
          await promoteIssue(c.env, issueId, payload.sender.login);
        } catch (error) {
          if (
            error instanceof Error &&
            error.message === "promotion_in_progress"
          ) {
            return;
          }
          const message =
            error instanceof Error ? error.message : "promotion_failed";
          if (message === "promotion_in_progress") {
            await c.env.DB.prepare(
              "UPDATE github_webhook_deliveries SET state = 'done', updated_at = datetime('now') WHERE delivery_id = ? AND state = 'processing' AND attempt_token = ?",
            )
              .bind(deliveryId, attemptToken)
              .run();
            return c.json({ ok: true, promotion: "in_progress" }, 202);
          }
          await c.env.DB.prepare(
            "UPDATE issues SET github_sync_state = 'promotion_pending', github_sync_error = ?, approved_by = COALESCE(approved_by, ?) WHERE id = ?",
          )
            .bind(message.slice(0, 200), payload.sender.login, issueId)
            .run();
          await c.env.DB.prepare(
            "UPDATE github_webhook_deliveries SET state = 'failed', error = ?, updated_at = datetime('now') WHERE delivery_id = ? AND state = 'processing' AND attempt_token = ?",
          )
            .bind(message.slice(0, 200), deliveryId, attemptToken)
            .run();
          console.error("issue_github_approval_failed", { issueId, message });
          return c.json({ error: "promotion_failed" }, 502);
        }
      } else if (approved) {
        await c.env.DB.prepare(
          "UPDATE issues SET github_sync_state = 'approval_pending' WHERE id = ? AND core_number IS NULL AND github_sync_state NOT IN ('promotion_pending', 'promotion_failed', 'approval_error', 'promoted')",
        )
          .bind(issueId)
          .run();
      }
    }
    if (
      ["created", "edited", "deleted"].includes(payload.action ?? "") &&
      payload.comment
    ) {
      await c.env.DB.prepare(
        `INSERT INTO issue_comments (id, issue_id, github_comment_id, author, body, html_url, created_at, updated_at, deleted_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(issue_id, github_comment_id) DO UPDATE SET author = excluded.author, body = excluded.body, html_url = excluded.html_url, updated_at = excluded.updated_at, deleted_at = excluded.deleted_at`,
      )
        .bind(
          crypto.randomUUID(),
          issueId,
          payload.comment.id,
          payload.comment.user?.login ?? "github-user",
          payload.comment.body ?? "",
          payload.comment.html_url ?? null,
          payload.comment.created_at ?? new Date().toISOString(),
          new Date().toISOString(),
          payload.action === "deleted" ? new Date().toISOString() : null,
        )
        .run();
    }
    await c.env.DB.prepare(
      "UPDATE github_webhook_deliveries SET state = 'done', updated_at = datetime('now') WHERE delivery_id = ? AND state = 'processing' AND attempt_token = ?",
    )
      .bind(deliveryId, attemptToken)
      .run();
    return c.json({ ok: true });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "webhook_processing_failed";
    await c.env.DB.prepare(
      "UPDATE github_webhook_deliveries SET state = 'failed', error = ?, updated_at = datetime('now') WHERE delivery_id = ? AND state = 'processing' AND attempt_token = ?",
    )
      .bind(message.slice(0, 200), deliveryId, attemptToken)
      .run();
    console.error("github_webhook_processing_failed", { deliveryId, message });
    return c.json({ error: "webhook_processing_failed" }, 500);
  }
});

// Serve um anexo do R2. Público por design: a key contém UUID e não é
// enumerável; quem tem a key veio da listagem pública da issue. Declarado
// ANTES de GET /:id — "files" nunca deve casar com o param de id.
issues.get("/files/*", async (c) => {
  const key = c.req.path.replace(/^.*?\/files\//, "");
  if (!key.startsWith("issues/")) return c.text("not found", 404);
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

// Detalhe público de uma issue (página /issues/$issueId no company). Mesma
// projeção sem `email` da listagem — privacidade do reporter. Arquivada
// (soft-delete) responde 404 igual a inexistente — quem precisa ver mesmo
// arquivada é admin, via GET /admin/issues/:id.
issues.get("/:id", async (c) => {
  const row = await c.env.DB.prepare(
    "SELECT id, title, category, description, files, status, created_at, github_url, github_sync_state, core_url FROM issues WHERE id = ? AND archived_at IS NULL",
  )
    .bind(c.req.param("id"))
    .first<{ files: string | null } & Record<string, unknown>>();
  if (!row) return c.json({ error: "not_found" }, 404);
  const { results: comments } = await c.env.DB.prepare(
    "SELECT author, body, html_url, created_at, updated_at FROM issue_comments WHERE issue_id = ? AND deleted_at IS NULL ORDER BY created_at ASC",
  )
    .bind(c.req.param("id"))
    .all();
  return c.json({
    ...row,
    files: row.files ? (JSON.parse(row.files) as string[]) : [],
    comments,
  });
});

issues.post("/waitlist", async (c) => {
  const body = await c.req.json<{
    email?: string;
    source?: string;
    turnstileToken?: string;
  }>();

  if (!body.email || !body.email.includes("@")) {
    return c.json({ error: "invalid_email" }, 400);
  }
  if (!body.turnstileToken) return c.json({ error: "turnstile_required" }, 400);

  const turnstile = await verifyTurnstile(
    body.turnstileToken,
    c.env.TURNSTILE_SECRET_KEY,
    c.req.header("cf-connecting-ip"),
  );
  if (!turnstile.success) return c.json({ error: "turnstile_failed" }, 400);

  try {
    await c.env.DB.prepare(
      "INSERT INTO waitlist (id, email, source) VALUES (?, ?, ?)",
    )
      .bind(crypto.randomUUID(), body.email.toLowerCase(), body.source ?? null)
      .run();

    await enqueueEmail(c.env, {
      to: body.email,
      subject: "Você está na lista — Vectora",
      html: waitlistJoinedHtml(),
    });
  } catch {
    // já cadastrado (email UNIQUE) — idempotente do ponto de vista do
    // usuário, não é erro visível.
  }

  return c.json({ ok: true });
});
