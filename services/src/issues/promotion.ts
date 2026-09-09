import type { Env } from "../gateway/types";
import {
  addComment,
  coreRepo,
  createIssue,
  findCommentByMarker,
  findIssueByMarker,
  intakeRepo,
  updateIssue,
} from "./github";

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
    "UPDATE issues SET approved_at = datetime('now') WHERE id = ? AND github_sync_state = 'promotion_pending' AND github_sync_error = ?",
  )
    .bind(issueId, operationToken)
    .run();
  if (renewed.meta.changes === 0) throw new Error("promotion_lost");
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
        "UPDATE issues SET github_sync_state = 'promotion_pending', approved_at = datetime('now') WHERE id = ? AND github_sync_state = 'promotion_failed' AND github_sync_error = ?",
      )
        .bind(issueId, claimToken)
        .run()
    : await env.DB.prepare(
        "UPDATE issues SET github_sync_state = 'promotion_pending', github_sync_error = ?, approved_at = datetime('now'), approved_by = COALESCE(approved_by, ?) WHERE id = ? AND github_sync_state NOT IN ('promotion_pending', 'promoted')",
      )
        .bind(operationToken, approvedBy, issueId)
        .run();
  if (claimed.meta.changes === 0) {
    throw new Error("promotion_in_progress");
  }
  let created =
    issue.core_number && issue.core_url
      ? { number: issue.core_number, html_url: issue.core_url }
      : await findIssueByMarker(env, targetRepo, marker);
  if (!created) {
    created = await createIssue(env, targetRepo, issue.title, body);
  }
  const persisted = await env.DB.prepare(
    "UPDATE issues SET core_repo = ?, core_number = ?, core_url = ?, approved_at = COALESCE(approved_at, datetime('now')), approved_by = COALESCE(approved_by, ?), github_sync_state = 'promotion_pending', github_sync_error = ? WHERE id = ? AND github_sync_error = ?",
  )
    .bind(
      targetRepo,
      created.number,
      created.html_url,
      approvedBy,
      operationToken,
      issueId,
      operationToken,
    )
    .run();
  if (persisted.meta.changes === 0) throw new Error("promotion_lost");
  await renewPromotionLease(env, issueId, operationToken);

  if (issue.github_repo && issue.github_number) {
    const backlinkMarker = `vectora-company-promotion:${issue.id}`;
    await renewPromotionLease(env, issueId, operationToken);
    const existingBacklink = await findCommentByMarker(
      env,
      issue.github_repo,
      issue.github_number,
      backlinkMarker,
    );
    if (!existingBacklink) {
      await renewPromotionLease(env, issueId, operationToken);
      await addComment(
        env,
        issue.github_repo,
        issue.github_number,
        `<!-- ${backlinkMarker} -->\nAprovada pela Company e promovida ao repositório principal: ${created.html_url}`,
      );
    }
    await renewPromotionLease(env, issueId, operationToken);
    await updateIssue(env, issue.github_repo, issue.github_number, {
      state: "closed",
    });
  }
  await env.DB.prepare(
    "UPDATE issues SET github_sync_state = 'promoted', github_sync_error = NULL WHERE id = ? AND github_sync_error = ?",
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
      "WHERE github_sync_state IN ('promotion_pending', 'approval_error') " +
      "AND approved_by IS NOT NULL " +
      "AND (github_sync_state = 'approval_error' OR approved_at IS NULL OR approved_at <= datetime('now', '-5 minutes')) " +
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
        "UPDATE issues SET github_sync_state = 'promotion_failed', github_sync_error = ? " +
          "WHERE id = ? AND github_sync_state = ? " +
          "AND (github_sync_state = 'approval_error' OR approved_at IS NULL OR approved_at <= datetime('now', '-5 minutes'))",
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
