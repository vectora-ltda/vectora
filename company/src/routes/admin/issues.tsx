import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { m } from "#/paraglide/messages";
import { listIssuesAdmin } from "#/server/fns/admin";

export const Route = createFileRoute("/admin/issues")({
  component: AdminIssuesPage,
});

const CATEGORIES = ["bug", "feedback", "feature"] as const;
type Category = (typeof CATEGORIES)[number];

const CATEGORY_LABELS: Record<Category, string> = {
  bug: "Bug",
  feedback: "Feedback",
  feature: "Feature request",
};

function AdminIssuesPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["admin-issues"],
    queryFn: () => listIssuesAdmin({ data: {} }),
  });

  if (isLoading) {
    return <div className="h-40 rounded-xl bg-card/30 animate-pulse" />;
  }

  const issues = data?.issues ?? [];

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold text-foreground">
        {m.admin_issues_title()}
      </h1>
      {issues.length === 0 ? (
        <div className="rounded-xl border border-border bg-card/10 p-8 text-center text-sm text-muted-foreground">
          {m.admin_issues_empty()}
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-card/50">
                {[
                  m.admin_col_title(),
                  m.admin_col_category(),
                  m.admin_col_email(),
                  m.admin_col_status(),
                ].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-3 text-start text-xs font-medium text-muted-foreground"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {issues.map((issue, i) => (
                <tr
                  key={issue.id}
                  className={`border-b border-border ${i % 2 === 0 ? "" : "bg-background/20"}`}
                >
                  <td className="px-4 py-3 font-medium text-foreground">
                    <Link
                      to="/issues/$issueId"
                      params={{ issueId: issue.id }}
                      className="hover:underline"
                    >
                      {issue.title}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {CATEGORY_LABELS[issue.category]}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {issue.email ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {issue.status === "resolved"
                      ? m.issues_status_resolved()
                      : m.issues_status_open()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
