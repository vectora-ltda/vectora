import { useState } from "react";
import { createFileRoute, Link, useRouter } from "@tanstack/react-router";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { m } from "#/paraglide/messages";
import Container from "#/components/shared/Container";
import PageHeader from "#/components/shared/PageHeader";
import { getIssue } from "#/server/fns/issues";
import {
  getIssueAdmin,
  respondToIssue,
  archiveIssue,
  approveIssue,
  syncIssue,
} from "#/server/fns/admin";
import { resolveViewerRole } from "#/lib/auth/viewer";

export const Route = createFileRoute("/issues_/$issueId")({
  head: () => ({
    meta: [{ name: "robots", content: "noindex, nofollow" }],
  }),
  loader: async ({ params }) => {
    const { isAdmin } = await resolveViewerRole();
    if (isAdmin) {
      const issue = await getIssueAdmin({
        data: { id: params.issueId },
      }).catch(() => null);
      return { issue, isAdmin: true as const };
    }
    const issue = await getIssue({ data: { id: params.issueId } });
    return { issue, isAdmin: false as const };
  },
  component: IssueDetailPage,
});

const CATEGORIES = ["bug", "feedback", "feature"] as const;
type Category = (typeof CATEGORIES)[number];

const CATEGORY_LABELS: Record<Category, string> = {
  bug: "Bug",
  feedback: "Feedback",
  feature: "Feature request",
};

const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp"];
const VIDEO_EXTENSIONS = [".mp4", ".webm"];

function isImageUrl(url: string): boolean {
  const lower = url.toLowerCase();
  return IMAGE_EXTENSIONS.some((ext) => lower.includes(ext));
}

function isVideoUrl(url: string): boolean {
  const lower = url.toLowerCase();
  return VIDEO_EXTENSIONS.some((ext) => lower.includes(ext));
}

function AttachmentPreview({ url }: { url: string }) {
  if (isImageUrl(url)) {
    return (
      <a href={url} target="_blank" rel="noreferrer" className="block">
        <img
          src={url}
          alt=""
          className="max-h-64 rounded-lg border border-border object-contain"
        />
      </a>
    );
  }
  if (isVideoUrl(url)) {
    return (
      <a href={url} target="_blank" rel="noreferrer" className="block">
        <video
          src={url}
          controls
          className="max-h-64 rounded-lg border border-border"
        />
      </a>
    );
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="text-primary hover:underline"
    >
      {url}
    </a>
  );
}

function IssueDetailPage() {
  const { issue, isAdmin } = Route.useLoaderData();
  const { issueId } = Route.useParams();
  const router = useRouter();

  const [response, setResponse] = useState("");
  const [resolve, setResolve] = useState(true);

  const respondMutation = useMutation({
    mutationFn: () =>
      respondToIssue({ data: { id: issueId, response, resolve } }),
    onSuccess: () => {
      toast.success(m.admin_issue_response_sent());
      setResponse("");
      void router.invalidate();
    },
    onError: () => toast.error(m.error_generic()),
  });

  const archived = isAdmin && issue ? Boolean(issue.archived_at) : false;
  const archiveMutation = useMutation({
    mutationFn: () =>
      archiveIssue({ data: { id: issueId, archived: !archived } }),
    onSuccess: () => void router.invalidate(),
    onError: () => toast.error(m.error_generic()),
  });

  const approveMutation = useMutation({
    mutationFn: () => approveIssue({ data: { id: issueId } }),
    onSuccess: () => void router.invalidate(),
    onError: () => toast.error(m.error_generic()),
  });

  const syncMutation = useMutation({
    mutationFn: () => syncIssue({ data: { id: issueId } }),
    onSuccess: () => void router.invalidate(),
    onError: () => toast.error(m.error_generic()),
  });

  if (!issue) {
    return (
      <Container size="prose" className="py-16">
        <Link to="/issues" className="text-sm text-primary hover:underline">
          {m.issues_detail_back()}
        </Link>
        <p className="mt-6 text-sm text-muted-foreground">
          {m.issues_detail_not_found()}
        </p>
      </Container>
    );
  }

  return (
    <Container size="prose" className="py-16">
      <Link to="/issues" className="text-sm text-primary hover:underline">
        {m.issues_detail_back()}
      </Link>

      <div className="mt-6 mb-10">
        <PageHeader align="left" title={issue.title} />
      </div>

      <div className="rounded-xl border border-border bg-card/40 px-5 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
            {CATEGORY_LABELS[issue.category]}
          </span>
          <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
            {issue.status === "resolved"
              ? m.issues_status_resolved()
              : m.issues_status_open()}
          </span>
          {archived && (
            <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
              {m.admin_issue_archived()}
            </span>
          )}
          {isAdmin && issue.email && (
            <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
              {issue.email}
            </span>
          )}
          {issue.github_url && (
            <a
              href={issue.github_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-primary hover:underline"
            >
              GitHub intake ↗
            </a>
          )}
          {issue.core_url && (
            <a
              href={issue.core_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-primary hover:underline"
            >
              Core issue ↗
            </a>
          )}
        </div>

        {issue.description && (
          <p className="mt-4 whitespace-pre-wrap text-sm text-foreground/90">
            {issue.description}
          </p>
        )}

        {issue.files.length > 0 && (
          <div className="mt-4">
            <p className="mb-2 text-[11px] text-muted-foreground/70">
              {m.issues_attachments()}:
            </p>
            <div className="flex flex-wrap gap-3">
              {issue.files.map((url) => (
                <AttachmentPreview key={url} url={url} />
              ))}
            </div>
          </div>
        )}

        <time className="mt-4 block text-[11px] text-muted-foreground/70">
          {new Date(issue.created_at).toLocaleDateString()}
        </time>
      </div>

      {issue.comments && issue.comments.length > 0 && (
        <div className="mt-6 space-y-3">
          <h2 className="text-sm font-semibold text-foreground">
            GitHub discussion
          </h2>
          {issue.comments.map((comment) => (
            <div
              key={`${comment.author}-${comment.created_at}`}
              className="rounded-xl border border-border bg-card/30 p-4"
            >
              <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
                <span>{comment.author}</span>
                <time>{new Date(comment.created_at).toLocaleString()}</time>
              </div>
              <p className="mt-2 whitespace-pre-wrap text-sm text-foreground/90">
                {comment.body}
              </p>
              {comment.html_url && (
                <a
                  href={comment.html_url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 inline-block text-xs text-primary hover:underline"
                >
                  View on GitHub ↗
                </a>
              )}
            </div>
          ))}
        </div>
      )}

      {isAdmin && (
        <div className="mt-6 space-y-4">
          {issue.response && (
            <div className="rounded-xl border border-border bg-card/30 p-4">
              <p className="mb-1 text-xs font-medium text-muted-foreground">
                {m.admin_issue_response_existing()}
              </p>
              <p className="whitespace-pre-wrap text-sm text-foreground/90">
                {issue.response}
              </p>
            </div>
          )}

          <div className="space-y-3 rounded-xl border border-border bg-card/30 p-4">
            <label
              htmlFor="issue-response"
              className="block text-xs font-medium text-muted-foreground"
            >
              {m.admin_issue_response_label()}
            </label>
            <textarea
              id="issue-response"
              rows={4}
              value={response}
              onChange={(e) => setResponse(e.target.value)}
              placeholder={m.admin_issue_response_placeholder()}
              className="w-full resize-none rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={resolve}
                onChange={(e) => setResolve(e.target.checked)}
              />
              {m.admin_issue_mark_resolved()}
            </label>
            <div className="flex gap-2">
              <button
                onClick={() => respondMutation.mutate()}
                disabled={
                  response.trim().length < 3 || respondMutation.isPending
                }
                className="flex-1 rounded-lg bg-primary py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-all"
              >
                {respondMutation.isPending
                  ? m.form_submitting()
                  : m.admin_issue_response_submit()}
              </button>
              {!issue.core_url && (
                <button
                  onClick={() => approveMutation.mutate()}
                  disabled={approveMutation.isPending}
                  className="rounded-lg border border-primary px-4 py-2 text-sm text-primary hover:bg-primary/10 disabled:opacity-40 transition-colors"
                >
                  {approveMutation.isPending
                    ? "Promoting…"
                    : "Approve for core"}
                </button>
              )}
              {!issue.github_url && (
                <button
                  onClick={() => syncMutation.mutate()}
                  disabled={syncMutation.isPending}
                  className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40 transition-colors"
                >
                  {syncMutation.isPending ? "Syncing…" : "Retry GitHub sync"}
                </button>
              )}
              <button
                onClick={() => archiveMutation.mutate()}
                disabled={archiveMutation.isPending}
                className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40 transition-colors"
              >
                {archived ? m.admin_issue_unarchive() : m.admin_issue_archive()}
              </button>
            </div>
          </div>
        </div>
      )}
    </Container>
  );
}
