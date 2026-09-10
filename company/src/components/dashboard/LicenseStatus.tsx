import { useMutation, useQuery } from "@tanstack/react-query";
import { m } from "#/paraglide/messages";
import {
  getSubscription,
  getLicenseHistory,
  createCheckout,
  createPortal,
} from "#/server/fns/subscription";
import type { LicenseCheck } from "#/server/fns/subscription";
import { toast } from "sonner";

// "trialing" existe no CHECK constraint da coluna (schema legado do modelo
// antigo de trial de 30 dias do Plus) mas nunca é gravado por nenhum fluxo
// atual — signup sempre grava "active" (Free permanente) e os webhooks de
// billing só transitam entre active/past_due/canceled. Não modelado aqui de
// propósito: não há UI pra um estado que o backend nunca produz.
type SubStatus = "active" | "past_due" | "canceled" | "expired";

function useStatusConfig(): Record<
  SubStatus,
  { label: string; color: string }
> {
  return {
    active: {
      label: m.license_status_active(),
      color: "text-accent-green bg-accent-green/10 border-accent-green/30",
    },
    past_due: {
      label: m.license_status_past_due(),
      color: "text-accent-amber bg-accent-amber/10 border-accent-amber/30",
    },
    canceled: {
      label: m.license_status_canceled(),
      color: "text-accent-red bg-accent-red/10 border-accent-red/30",
    },
    expired: {
      label: m.license_status_expired(),
      color: "text-muted-foreground bg-muted border-border",
    },
  };
}

function isSubStatus(
  value: string,
  config: Record<string, unknown>,
): value is SubStatus {
  return value in config;
}

function maskIp(ip: string | null) {
  if (!ip) return "—";
  const parts = ip.split(".");
  if (parts.length === 4) return `${parts[0]}.${parts[1]}.*.*`;
  return ip.slice(0, 8) + "...";
}

export function LicenseStatus() {
  const { data: sub, isLoading } = useQuery({
    queryKey: ["subscription"],
    queryFn: () => getSubscription(),
    staleTime: 30_000,
  });

  const checkoutMutation = useMutation({
    mutationFn: () => createCheckout({ data: { planId: "1m" } }),
    onSuccess: (res) => {
      if ("redeemed" in res) return;
      window.location.href = res.url;
    },
    onError: () => toast.error(m.error_generic()),
  });

  const portalMutation = useMutation({
    mutationFn: () => createPortal(),
    onSuccess: (res) => {
      window.location.href = res.url;
    },
    onError: () => toast.error(m.error_generic()),
  });

  if (isLoading) {
    return <div className="h-32 rounded-xl bg-card/30 animate-pulse" />;
  }

  if (!sub) return null;

  // `status`/`tier` vêm do banco como `string` genérico (o gerador de types
  // não produz union literal pra CHECK constraints alterados via migration
  // posterior à criação da tabela) — fallback seguro se vier um valor
  // inesperado.
  const statusConfig = useStatusConfig();
  const status = sub.status;
  const config = isSubStatus(status, statusConfig)
    ? statusConfig[status]
    : statusConfig.expired;
  const isPro = sub.tier === "pro";
  const isPastDue = status === "past_due";
  const isPortalBusy = portalMutation.isPending;
  const isCheckoutBusy = checkoutMutation.isPending;

  return (
    <div className="max-w-2xl space-y-5">
      <div className="rounded-xl border border-border bg-card/30 p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm text-muted-foreground mb-1">
              {m.license_plan()}
            </p>
            <p className="text-xl font-semibold text-foreground capitalize">
              {sub.tier}
            </p>
          </div>
          <span
            className={`rounded-full border px-3 py-1 text-xs font-medium ${config.color}`}
          >
            {config.label}
          </span>
        </div>

        {sub.started_at && (
          <div className="mt-4 flex flex-wrap gap-4 text-sm">
            <div>
              <p className="text-xs text-muted-foreground">
                {m.license_started()}
              </p>
              <p className="text-foreground/90">
                {new Date(sub.started_at).toLocaleDateString()}
              </p>
            </div>
          </div>
        )}

        {/* CTAs — free é sempre utilizável (sem trial/expiração); só existe
            um upgrade possível (Pro). Portal só faz sentido pra quem já é
            pro (nada a gerenciar no free). */}
        <div className="mt-5 flex flex-wrap gap-2">
          {!isPro && (
            <button
              onClick={() => checkoutMutation.mutate()}
              disabled={isCheckoutBusy}
              className="rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-all"
            >
              {m.license_cta_upgrade_pro()}
            </button>
          )}
          {isPro && !isPastDue && (
            <button
              onClick={() => portalMutation.mutate()}
              disabled={isPortalBusy}
              className="rounded-xl border border-border px-4 py-2 text-sm font-medium text-muted-foreground hover:border-primary hover:text-foreground disabled:opacity-50 transition-all"
            >
              {isPortalBusy ? m.form_submitting() : m.license_cta_manage()}
            </button>
          )}
          {isPro && isPastDue && (
            <button
              onClick={() => portalMutation.mutate()}
              disabled={isPortalBusy}
              className="rounded-xl bg-accent-amber px-4 py-2 text-sm font-semibold text-foreground hover:bg-accent-amber disabled:opacity-50"
            >
              {m.license_cta_update_payment()}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function LicenseHistory() {
  const { data, isLoading } = useQuery({
    queryKey: ["license-checks"],
    queryFn: () => getLicenseHistory(),
    staleTime: 5 * 60_000,
  });

  if (isLoading)
    return <div className="h-24 rounded-xl bg-card/30 animate-pulse" />;

  if (!data?.length) {
    return (
      <div className="rounded-xl border border-border bg-card/10 p-6 text-center text-sm text-muted-foreground">
        {m.license_no_checks()}
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-card/50">
            <th className="px-4 py-3 text-start text-xs font-medium text-muted-foreground">
              {m.license_col_date()}
            </th>
            <th className="px-4 py-3 text-start text-xs font-medium text-muted-foreground">
              {m.license_col_version()}
            </th>
            <th className="px-4 py-3 text-start text-xs font-medium text-muted-foreground">
              {m.license_col_result()}
            </th>
            <th className="px-4 py-3 text-start text-xs font-medium text-muted-foreground">
              {m.license_col_ip()}
            </th>
          </tr>
        </thead>
        <tbody>
          {data.map((row: LicenseCheck, i: number) => (
            <tr
              key={row.id}
              className={`border-b border-border ${i % 2 === 0 ? "" : "bg-background/20"}`}
            >
              <td className="px-4 py-2.5 text-muted-foreground">
                {new Date(row.checked_at).toLocaleString()}
              </td>
              <td className="px-4 py-2.5 font-mono text-muted-foreground">
                {row.vectora_version}
              </td>
              <td className="px-4 py-2.5">
                <span
                  className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                    row.result === "valid"
                      ? "bg-accent-green/10 text-accent-green"
                      : "bg-accent-red/10 text-accent-red"
                  }`}
                >
                  {row.result}
                </span>
              </td>
              <td className="px-4 py-2.5 font-mono text-muted-foreground">
                {maskIp(row.ip)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
