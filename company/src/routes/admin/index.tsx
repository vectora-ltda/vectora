import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { m } from "#/paraglide/messages";
import { listUsers } from "#/server/fns/admin";

export const Route = createFileRoute("/admin/")({
  component: AdminUsersPage,
});

function AdminUsersPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => listUsers({ data: {} }),
  });

  if (isLoading) {
    return <div className="h-40 rounded-xl bg-card/30 animate-pulse" />;
  }

  const users = data?.users ?? [];

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold text-foreground">
        {m.admin_users_title()}
      </h1>
      {users.length === 0 ? (
        <div className="rounded-xl border border-border bg-card/10 p-8 text-center text-sm text-muted-foreground">
          {m.admin_users_empty()}
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-card/50">
                {[
                  m.admin_col_name(),
                  m.admin_col_email(),
                  m.admin_col_tier(),
                  m.admin_col_status(),
                  m.admin_col_expires(),
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
              {users.map((u, i) => (
                <tr
                  key={u.id}
                  className={`border-b border-border ${i % 2 === 0 ? "" : "bg-background/20"}`}
                >
                  <td className="px-4 py-3 font-medium text-foreground">
                    {u.full_name || "—"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{u.email}</td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {u.tier ?? "free"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {u.status ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {u.current_period_end
                      ? new Date(u.current_period_end).toLocaleDateString()
                      : u.tier === "pro"
                        ? m.admin_lifetime()
                        : "—"}
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
