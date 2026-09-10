import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { m } from "#/paraglide/messages";
import { listGifts, createGift } from "#/server/fns/admin";
import { PLANS } from "#/server/fns/subscription";

export const Route = createFileRoute("/admin/gifts")({
  component: AdminGiftsPage,
});

const LIFETIME = "lifetime";

function AdminGiftsPage() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["admin-gifts"],
    queryFn: () => listGifts(),
  });

  const [email, setEmail] = useState("");
  const [duration, setDuration] = useState<string>(LIFETIME);

  const createMutation = useMutation({
    mutationFn: () =>
      createGift({
        data: {
          email,
          duration_months: duration === LIFETIME ? undefined : Number(duration),
        },
      }),
    onSuccess: () => {
      toast.success(m.admin_gifts_created());
      setEmail("");
      queryClient.invalidateQueries({ queryKey: ["admin-gifts"] });
    },
    onError: () => toast.error(m.error_generic()),
  });

  const gifts = data?.gifts ?? [];

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold text-foreground">
        {m.admin_gifts_title()}
      </h1>

      <div className="mb-6 max-w-md space-y-3 rounded-xl border border-border bg-card/30 p-4">
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder={m.admin_gifts_email_label()}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />
        <select
          value={duration}
          onChange={(e) => setDuration(e.target.value)}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        >
          <option value={LIFETIME}>{m.admin_gifts_duration_lifetime()}</option>
          {PLANS.map((p) => (
            <option key={p.id} value={p.months}>
              {p.months} {m.admin_gifts_months_suffix()}
            </option>
          ))}
        </select>
        <button
          onClick={() => createMutation.mutate()}
          disabled={!email.includes("@") || createMutation.isPending}
          className="w-full rounded-lg bg-primary py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-all"
        >
          {createMutation.isPending
            ? m.form_submitting()
            : m.admin_gifts_create_button()}
        </button>
      </div>

      {isLoading ? (
        <div className="h-32 rounded-xl bg-card/30 animate-pulse" />
      ) : (
        <div className="overflow-hidden rounded-xl border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-card/50">
                {[
                  m.admin_col_email(),
                  m.admin_gifts_duration_label(),
                  m.admin_col_status(),
                  m.admin_col_granted_by(),
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
              {gifts.map((g, i) => (
                <tr
                  key={g.id}
                  className={`border-b border-border ${i % 2 === 0 ? "" : "bg-background/20"}`}
                >
                  <td className="px-4 py-3 text-foreground">{g.email}</td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {g.duration_months
                      ? `${g.duration_months} ${m.admin_gifts_months_suffix()}`
                      : m.admin_gifts_duration_lifetime()}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {g.status}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {g.granted_by_email}
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
