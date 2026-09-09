import type { Env } from "../gateway/types";
import {
  addComment,
  coreRepo,
  createIssue,
  findIssueByMarker,
  intakeRepo,
  updateIssue,
} from "./github";

export interface PromotionResult {
  url: string;
  number: number;
  alreadyPromoted?: boolean;
}

/** Promove uma issue da Company para o repositório privado principal da Vectora. */
export async function promoteIssue(
  env: Env,
  issueId: string,
  approvedBy: string,
): Promise<PromotionResult> {
  const issue = await env.DB.prepare(
    "SELECT id, title, description, github_repo, github_number, github_url, core_number, core_url FROM issues WHERE id = ?",
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
    }>();
  if (!issue) throw new Error("issue_not_found");
  if (issue.core_number && issue.core_url) {
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
  const targetRepo = coreRepo(env);
  const marker = `vectora-company-issue:${issue.id}`;
  const existingRemote = await findIssueByMarker(env, targetRepo, marker);
  const claimed = await env.DB.prepare(
    "UPDATE issues SET github_sync_state = 'promotion_pending', github_sync_error = NULL WHERE id = ? AND core_number IS NULL AND github_sync_state != 'promotion_pending'",
  )
    .bind(issueId)
    .run();
  if (claimed.meta.changes === 0 && !existingRemote) {
    throw new Error("promotion_in_progress");
  }
  const created =
    existingRemote ?? (await createIssue(env, targetRepo, issue.title, body));
  await env.DB.prepare(
    "UPDATE issues SET core_repo = ?, core_number = ?, core_url = ?, approved_at = datetime('now'), approved_by = ?, github_sync_state = 'promoted', github_sync_error = NULL WHERE id = ? AND core_number IS NULL",
  )
    .bind(targetRepo, created.number, created.html_url, approvedBy, issueId)
    .run();

  if (issue.github_repo && issue.github_number) {
    await addComment(
      env,
      issue.github_repo,
      issue.github_number,
      `Aprovada pela Company e promovida ao repositório principal: ${created.html_url}`,
    );
    await updateIssue(env, issue.github_repo, issue.github_number, {
      state: "closed",
    });
  }
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
