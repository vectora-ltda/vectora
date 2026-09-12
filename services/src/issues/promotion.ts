import type { Env } from "../gateway/types";
import {
  addComment,
  coreRepo,
  createIssue,
  findCommentByMarker,
  findIssueByMarker,
  authenticatedLogin,
  intakeRepo,
  updateIssue,
} from "./github";

const PROMOTION_LEASE_MINUTES = 10;
const PROMOTION_LEASE_HEARTBEAT_MS = 30_000;
const PROMOTION_EFFECT_STALE_MINUTES = 15;

type PromotionEffect = "backlink" | "close";

export interface PromotionResult {
  url: string;
  number: number;
  alreadyPromoted?: boolean;
}

async function renewPromotionLease(
  env: Env,
  issueId: string,
  operationToken: string,
): Promise<void> {
  const renewed = await env.DB.prepare(
    `UPDATE issues SET promotion_lease_until = datetime('now', '+${PROMOTION_LEASE_MINUTES} minutes')
     WHERE id = ? AND github_sync_state = 'promotion_pending'
     AND promotion_operation_token = ?`,
  )
    .bind(issueId, operationToken)
    .run();
  if (renewed.meta.changes === 0) throw new Error("promotion_lost");
}

/** Executa uma chamada externa mantendo o lease renovado durante a espera. */
async function withPromotionLease<T>(
  env: Env,
  issueId: string,
  operationToken: string,
  operation: () => Promise<T>,
): Promise<T> {
  await renewPromotionLease(env, issueId, operationToken);
  let leaseError: Error | undefined;
  const heartbeat = setInterval(() => {
    void renewPromotionLease(env, issueId, operationToken).catch(
      (error: unknown) => {
        leaseError =
          error instanceof Error ? error : new Error("promotion_lost");
      },
    );
  }, PROMOTION_LEASE_HEARTBEAT_MS);
  try {
    const result = await operation();
    if (leaseError) throw leaseError;
    await renewPromotionLease(env, issueId, operationToken);
    return result;
  } finally {
    clearInterval(heartbeat);
  }
}

type PromotionEffectClaim = "acquired" | "completed" | "active";

/** Reserva um efeito externo para impedir duplicação entre reconciliadores. */
async function claimPromotionEffect(
  env: Env,
  issueId: string,
  effect: PromotionEffect,
  operationToken: string,
): Promise<PromotionEffectClaim> {
  const inserted = await env.DB.prepare(
    `INSERT OR IGNORE INTO issue_promotion_effects
      (issue_id, effect, operation_token)
     VALUES (?, ?, ?)`,
  )
    .bind(issueId, effect, operationToken)
    .run();
  if (inserted.meta.changes > 0) return "acquired";

  const current = await env.DB.prepare(
    "SELECT operation_token, completed_at, started_at FROM issue_promotion_effects WHERE issue_id = ? AND effect = ?",
  )
    .bind(issueId, effect)
    .first<{
      operation_token: string;
      completed_at: string | null;
      started_at: string;
    }>();
  if (!current) return "active";
  if (current.completed_at) return "completed";
  const reclaimed = await env.DB.prepare(
    `UPDATE issue_promotion_effects
     SET operation_token = ?, started_at = datetime('now')
     WHERE issue_id = ? AND effect = ? AND completed_at IS NULL
       AND started_at <= datetime('now', ?)`,
  )
    .bind(
      operationToken,
      issueId,
      effect,
      `-${PROMOTION_EFFECT_STALE_MINUTES} minutes`,
    )
    .run();
  return reclaimed.meta.changes > 0 ? "acquired" : "active";
}

async function completePromotionEffect(
  env: Env,
  issueId: string,
  effect: PromotionEffect,
  operationToken: string,
): Promise<void> {
  await env.DB.prepare(
    `UPDATE issue_promotion_effects SET completed_at = datetime('now')
     WHERE issue_id = ? AND effect = ? AND operation_token = ?`,
  )
    .bind(issueId, effect, operationToken)
    .run();
}

/** Promove uma issue da Company para o repositório privado principal da Vectora. */
export async function promoteIssue(
  env: Env,
  issueId: string,
  approvedBy: string,
  claimToken?: string,
): Promise<PromotionResult> {
  const issue = await env.DB.prepare(
    "SELECT id, title, description, github_repo, github_number, github_url, core_repo, core_number, core_url, github_sync_state, github_sync_error FROM issues WHERE id = ?",
  )
    .bind(issueId)
    .first<{
      id: string;
      title: string;
      description: string | null;
      github_repo: string | null;
      github_number: number | null;
      github_url: string | null;
      core_number: number | null;
      core_url: string | null;
      core_repo: string | null;
      github_sync_state: string;
      github_sync_error: string | null;
    }>();
  if (!issue) throw new Error("issue_not_found");
  if (
    issue.core_number &&
    issue.core_url &&
    issue.github_sync_state === "promoted"
  ) {
    return {
      url: issue.core_url,
      number: issue.core_number,
      alreadyPromoted: true,
    };
  }
  if (!env.GITHUB_ISSUES_TOKEN && !env.GITHUB_TOKEN)
    throw new Error("github_not_configured");

  const sourceUrl =
    issue.github_url ??
    `https://github.com/${intakeRepo(env)}/issues/${issue.github_number ?? ""}`;
  const body = [
    `<!-- vectora-company-issue:${issue.id} -->`,
    `Promovida da issue pública: ${sourceUrl}`,
    `Solicitação criada na Company: https://vectora.company/issues/${issue.id}`,
    "",
    issue.description ?? "Sem descrição adicional.",
  ].join("\n");
  const targetRepo = issue.core_repo ?? coreRepo(env);
  const marker = `vectora-company-issue:${issue.id}`;
  const operationToken = claimToken ?? crypto.randomUUID();
  const claimed = claimToken
    ? await env.DB.prepare(
        "UPDATE issues SET github_sync_state = 'promotion_pending', promotion_lease_until = datetime('now', '+10 minutes') WHERE id = ? AND github_sync_state = 'promotion_failed' AND promotion_operation_token = ?",
      )
        .bind(issueId, claimToken)
        .run()
    : await env.DB.prepare(
        "UPDATE issues SET github_sync_state = 'promotion_pending', promotion_operation_token = ?, github_sync_error = NULL, approved_at = COALESCE(approved_at, datetime('now')), promotion_lease_until = datetime('now', '+10 minutes'), approved_by = COALESCE(approved_by, ?) WHERE id = ? AND github_sync_state NOT IN ('promotion_pending', 'promoted')",
      )
        .bind(operationToken, approvedBy, issueId)
        .run();
  if (claimed.meta.changes === 0) {
    throw new Error("promotion_in_progress");
  }
  let created =
    issue.core_number && issue.core_url
      ? { number: issue.core_number, html_url: issue.core_url }
      : await withPromotionLease(env, issueId, operationToken, async () => {
          const candidates = await findIssueByMarker(env, targetRepo, marker);
          if (candidates.length === 0) return null;
          const authenticated =
            env.GITHUB_ISSUES_BOT_LOGIN?.trim() ||
            (await authenticatedLogin(env));
          return (
            candidates.find(
              (candidate) =>
                candidate.user?.login?.toLowerCase() ===
                authenticated.toLowerCase(),
            ) ?? null
          );
        });
  if (!created) {
    created = await withPromotionLease(env, issueId, operationToken, () =>
      createIssue(env, targetRepo, issue.title, body),
    );
  }
  const persisted = await env.DB.prepare(
    "UPDATE issues SET core_repo = ?, core_number = ?, core_url = ?, approved_at = COALESCE(approved_at, datetime('now')), promotion_lease_until = datetime('now', '+10 minutes'), approved_by = COALESCE(approved_by, ?), github_sync_state = 'promotion_pending', github_sync_error = NULL WHERE id = ? AND promotion_operation_token = ?",
  )
    .bind(
      targetRepo,
      created.number,
      created.html_url,
      approvedBy,
      issueId,
      operationToken,
    )
    .run();
  if (persisted.meta.changes === 0) throw new Error("promotion_lost");
  await renewPromotionLease(env, issueId, operationToken);

  if (issue.github_repo && issue.github_number) {
    const backlinkMarker = `vectora-company-promotion:${issue.id}`;
    const existingBacklink = await withPromotionLease(
      env,
      issueId,
      operationToken,
      () =>
        findCommentByMarker(
          env,
          issue.github_repo as string,
          issue.github_number as number,
          backlinkMarker,
        ),
    );
    if (!existingBacklink) {
      const claim = await claimPromotionEffect(
        env,
        issueId,
        "backlink",
        operationToken,
      );
      if (claim === "active") throw new Error("promotion_in_progress");
      if (claim === "acquired") {
        await withPromotionLease(env, issueId, operationToken, () =>
          addComment(
            env,
            issue.github_repo as string,
            issue.github_number as number,
            `<!-- ${backlinkMarker} -->\nAprovada pela Company e promovida ao repositório principal: ${created.html_url}`,
          ),
        );
        await completePromotionEffect(env, issueId, "backlink", operationToken);
      }
    }
    const closeClaim = await claimPromotionEffect(
      env,
      issueId,
      "close",
      operationToken,
    );
    if (closeClaim === "active") throw new Error("promotion_in_progress");
    if (closeClaim === "acquired") {
      await withPromotionLease(env, issueId, operationToken, () =>
        updateIssue(
          env,
          issue.github_repo as string,
          issue.github_number as number,
          { state: "closed" },
        ),
      );
      await completePromotionEffect(env, issueId, "close", operationToken);
    }
  }
  await env.DB.prepare(
    "UPDATE issues SET github_sync_state = 'promoted', github_sync_error = NULL WHERE id = ? AND promotion_operation_token = ?",
  )
    .bind(issueId, operationToken)
    .run();
  return { url: created.html_url, number: created.number };
}

/** Verifica se um ator do GitHub pode aprovar issues de entrada automaticamente. */
export function githubApprovalAllowed(env: Env, login: string): boolean {
  const configured = env.GITHUB_ISSUES_APPROVERS?.split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);
  return Boolean(
    configured?.length && configured.includes(login.trim().toLowerCase()),
  );
}

/** Retoma promoções que criaram a issue principal mas ainda não fecharam a pública. */
export async function reconcilePendingPromotions(env: Env): Promise<void> {
  const { results } = await env.DB.prepare(
    "SELECT id, approved_by, core_number, github_sync_state FROM issues " +
      "WHERE github_sync_state IN ('promotion_pending', 'approval_error', 'promotion_failed') " +
      "AND approved_by IS NOT NULL " +
      "AND (github_sync_state IN ('approval_error', 'promotion_failed') OR promotion_lease_until IS NULL OR promotion_lease_until <= datetime('now')) " +
      "LIMIT 25",
  ).all<{
    id: string;
    approved_by: string;
    core_number: number | null;
    github_sync_state: string;
  }>();
  for (const issue of results) {
    try {
      const claimToken = crypto.randomUUID();
      const claimed = await env.DB.prepare(
        "UPDATE issues SET github_sync_state = 'promotion_failed', promotion_operation_token = ?, github_sync_error = NULL " +
          "WHERE id = ? AND github_sync_state = ? " +
          "AND (github_sync_state IN ('approval_error', 'promotion_failed') OR promotion_lease_until IS NULL OR promotion_lease_until <= datetime('now'))",
      )
        .bind(claimToken, issue.id, issue.github_sync_state)
        .run();
      if (claimed.meta.changes === 0) continue;
      await promoteIssue(env, issue.id, issue.approved_by, claimToken);
    } catch (error) {
      console.error("issue_github_promotion_retry_failed", {
        issueId: issue.id,
        message: error instanceof Error ? error.message : "promotion_failed",
      });
    }
  }
}
