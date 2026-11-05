"use client";

/**
 * BillingPanel — categoria "Cobrança" do SettingsOverlay.
 *
 * Reloca o que `useLicenseStatus` já expõe (hoje só visível dentro do
 * `LicenseBanner`, que só aparece em estados de alerta) pra um lugar
 * fixo e sempre acessível — inclusive quando não há nada errado com a
 * licença.
 */

import { useState } from "react";
import { Loader2 } from "lucide-react";

import { useLicenseStatus } from "@/lib/hooks/use-license-status";
import { m } from "@/lib/paraglide/messages";

function openUrl(url: string): Promise<void> {
  if (typeof window !== "undefined" && window.vectora?.openExternal) {
    return window.vectora.openExternal(url);
  }
  window.open(url, "_blank", "noopener,noreferrer");
  return Promise.resolve();
}

const TIER_LABEL: Record<string, string> = {
  plus: "Vectora Plus",
  pro: "Vectora Pro",
};

export function BillingPanel() {
  const { status, loading } = useLicenseStatus();
  const [portalLoading, setPortalLoading] = useState(false);

  async function handlePortal() {
    if (portalLoading) return;
    setPortalLoading(true);
    try {
      const res = await fetch("/license/portal", { method: "POST" });
      if (!res.ok) {
        void openUrl("https://vectora.company/dashboard/billing");
        return;
      }
      const data = (await res.json()) as { url?: string };
      if (data.url) void openUrl(data.url);
    } finally {
      setPortalLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!status || !status.configured) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          {m.billing_panel_free()}
        </p>
        <button
          type="button"
          onClick={() =>
            void openUrl("https://vectora.company/dashboard/billing")
          }
          className="text-xs px-3 py-1.5 rounded-md border border-border hover:bg-muted/50 transition-colors"
        >
          {m.billing_panel_upgrade_button()}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-lg border bg-card p-2.5">
          <p className="text-[10px] text-muted-foreground">
            {m.billing_panel_plan_label()}
          </p>
          <p className="text-xs font-medium">
            {(status.tier && TIER_LABEL[status.tier]) || status.tier || "—"}
          </p>
        </div>
        <div className="rounded-lg border bg-card p-2.5">
          <p className="text-[10px] text-muted-foreground">
            {m.billing_panel_status_label()}
          </p>
          <p className="text-xs font-medium">{status.status}</p>
        </div>
      </div>

      {(status.status === "trial" || status.status === "trialing") && (
        <p className="text-xs text-muted-foreground">
          {m.billing_panel_days_remaining({ n: status.days_remaining })}
        </p>
      )}

      <button
        type="button"
        onClick={handlePortal}
        disabled={portalLoading}
        className="text-xs px-3 py-1.5 rounded-md border border-border hover:bg-muted/50 transition-colors disabled:opacity-50"
      >
        {portalLoading ? "…" : m.billing_panel_manage_button()}
      </button>
    </div>
  );
}
