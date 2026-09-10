import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { m } from "#/paraglide/messages";
import {
  listCoupons,
  createCoupon,
  deactivateCoupon,
} from "#/server/fns/admin";
import { PLANS } from "#/server/fns/subscription";

export const Route = createFileRoute("/admin/coupons")({
  component: AdminCouponsPage,
});

function AdminCouponsPage() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["admin-coupons"],
    queryFn: () => listCoupons(),
  });

  const [code, setCode] = useState("");
  const [kind, setKind] = useState<"discount" | "free_lifetime">("discount");
  const [grantPlanId, setGrantPlanId] = useState<string>(PLANS[1].id);
  const [chargePlanId, setChargePlanId] = useState<string>(PLANS[0].id);
  const [maxRedemptions, setMaxRedemptions] = useState("");

  const createMutation = useMutation({
    mutationFn: () =>
      createCoupon({
        data: {
          code,
          kind,
          grant_plan_id: kind === "discount" ? grantPlanId : undefined,
          charge_plan_id: kind === "discount" ? chargePlanId : undefined,
          max_redemptions: maxRedemptions ? Number(maxRedemptions) : undefined,
        },
      }),
    onSuccess: () => {
      toast.success(m.admin_coupons_created());
      setCode("");
      setMaxRedemptions("");
      queryClient.invalidateQueries({ queryKey: ["admin-coupons"] });
    },
    onError: () => toast.error(m.error_generic()),
  });

  const deactivateMutation = useMutation({
    mutationFn: (id: string) => deactivateCoupon({ data: { id } }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["admin-coupons"] }),
    onError: () => toast.error(m.error_generic()),
  });

  const coupons = data?.coupons ?? [];

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold text-foreground">
        {m.admin_coupons_title()}
      </h1>

      <div className="mb-6 max-w-md space-y-3 rounded-xl border border-border bg-card/30 p-4">
        <input
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder={m.admin_coupons_code_label()}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />
        <select
          value={kind}
          onChange={(e) =>
            setKind(e.target.value as "discount" | "free_lifetime")
          }
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        >
          <option value="discount">{m.admin_coupons_kind_discount()}</option>
          <option value="free_lifetime">
            {m.admin_coupons_kind_free_lifetime()}
          </option>
        </select>
        {kind === "discount" && (
          <div className="flex gap-2">
            <select
              value={chargePlanId}
              onChange={(e) => setChargePlanId(e.target.value)}
              className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm"
            >
              {PLANS.map((p) => (
                <option key={p.id} value={p.id}>
                  {m.admin_coupons_charge_plan_label()}: {p.id}
                </option>
              ))}
            </select>
            <select
              value={grantPlanId}
              onChange={(e) => setGrantPlanId(e.target.value)}
              className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm"
            >
              {PLANS.map((p) => (
                <option key={p.id} value={p.id}>
                  {m.admin_coupons_grant_plan_label()}: {p.id}
                </option>
              ))}
            </select>
          </div>
        )}
        <input
          type="number"
          value={maxRedemptions}
          onChange={(e) => setMaxRedemptions(e.target.value)}
          placeholder={m.admin_coupons_max_redemptions_label()}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />
        <button
          onClick={() => createMutation.mutate()}
          disabled={code.trim().length < 3 || createMutation.isPending}
          className="w-full rounded-lg bg-primary py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-all"
        >
          {createMutation.isPending
            ? m.form_submitting()
            : m.admin_coupons_create_button()}
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
                  m.admin_col_code(),
                  m.admin_col_kind(),
                  m.admin_col_redemptions(),
                  m.admin_col_status(),
                  "",
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
              {coupons.map((c, i) => (
                <tr
                  key={c.id}
                  className={`border-b border-border ${i % 2 === 0 ? "" : "bg-background/20"}`}
                >
                  <td className="px-4 py-3 font-mono text-foreground">
                    {c.code}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{c.kind}</td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {c.times_redeemed}
                    {c.max_redemptions ? `/${c.max_redemptions}` : ""}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {c.active ? m.admin_active() : m.admin_inactive()}
                  </td>
                  <td className="px-4 py-3">
                    {c.active ? (
                      <button
                        onClick={() => deactivateMutation.mutate(c.id)}
                        className="text-xs text-accent-red hover:text-destructive font-medium"
                      >
                        {m.admin_coupons_deactivate()}
                      </button>
                    ) : null}
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
