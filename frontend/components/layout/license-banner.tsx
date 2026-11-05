/**
 * Banner único de licença que aparece abaixo do header.
 *
 * Estados visuais derivados de ``GET /license/status``:
 * - **laranja** — sem token configurado ou pagamento em atraso (`past_due`).
 * - **amarelo** — trial expira em ≤7 dias.
 * - **vermelho** — licença expirada/revogada (bloqueia input no chat
 *   via prop `onBlockingChange`).
 * - **oculto** — licença ativa e fora da janela de aviso.
 *
 * Click em "Configurar" abre o painel de Administração → Config (P4);
 * "Renovar"/"Assinar" abre o Customer Portal via ``POST /license/portal``
 * em nova aba (web) ou ``window.vectora.openExternal`` (desktop Electron).
 */

import { useEffect, useState } from "react";
import { AlertTriangle, Clock, ShieldAlert, X } from "lucide-react";

import { useLicenseStatus } from "@/lib/hooks/use-license-status";
import { useAdministracaoDialogStore } from "@/lib/stores/administracao-dialog-store";
import { m } from "@/lib/paraglide/messages";

interface LicenseBannerProps {
  /** Se true, banner ocupa toda a largura abaixo do header. */
  fullWidth?: boolean;
  /** Notifica o pai quando o banner está em estado bloqueante (vermelho). */
  onBlockingChange?: (blocking: boolean) => void;
}

type Severity = "warning" | "danger" | "critical" | null;

interface BannerSpec {
  severity: Severity;
  icon: typeof AlertTriangle;
  message: string;
  cta: { label: string; action: "configure" | "portal" } | null;
}

const TRIAL_WARN_DAYS = 7;

export function LicenseBanner({
  fullWidth = true,
  onBlockingChange,
}: LicenseBannerProps) {
  const { status } = useLicenseStatus();
  const openAdmin = useAdministracaoDialogStore((s) => s.openAt);
  const [dismissed, setDismissed] = useState(false);
  const [portalLoading, setPortalLoading] = useState(false);

  const spec = computeSpec(status);

  useEffect(() => {
    onBlockingChange?.(spec.severity === "critical");
  }, [spec.severity, onBlockingChange]);

  if (!spec.severity || dismissed) return null;

  async function handlePortal() {
    if (portalLoading) return;
    setPortalLoading(true);
    try {
      const res = await fetch("/license/portal", { method: "POST" });
      if (!res.ok) {
        // Sem assinatura ativa → manda pro site de pricing.
        void openUrl("https://vectora.company/pricing");
        return;
      }
      const data = (await res.json()) as { url?: string };
      if (data.url) void openUrl(data.url);
    } finally {
      setPortalLoading(false);
    }
  }

  const Icon = spec.icon;
  const { bg, icon } = SEVERITY_COLORS[spec.severity];

  return (
    <div
      role="status"
      className={`${bg} text-foreground ${fullWidth ? "w-full" : ""} px-4 py-2 text-xs flex items-center gap-2 border-b`}
    >
      <Icon className={`${icon} w-3.5 h-3.5 shrink-0`} />
      <span className="flex-1 min-w-0">{spec.message}</span>
      {spec.cta && (
        <button
          type="button"
          onClick={
            spec.cta.action === "configure"
              ? () => openAdmin("system")
              : handlePortal
          }
          disabled={portalLoading}
          className="ml-2 px-2 py-0.5 rounded border border-current/40 hover:bg-current/10 transition-colors disabled:opacity-50"
        >
          {portalLoading ? "…" : spec.cta.label}
        </button>
      )}
      {spec.severity !== "critical" && (
        <button
          type="button"
          onClick={() => setDismissed(true)}
          aria-label={m.license_dismiss()}
          className="ml-1 p-0.5 rounded hover:bg-current/10"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
}

// Texto sempre usa a cor principal do tema (foreground) para garantir
// contraste em claro e escuro; só o fundo/borda/ícone variam por severidade.
const SEVERITY_COLORS: Record<
  Exclude<Severity, null>,
  { bg: string; icon: string }
> = {
  warning: {
    bg: "bg-amber-500/15 border-amber-500/30",
    icon: "text-amber-500",
  },
  danger: {
    bg: "bg-orange-500/15 border-orange-500/30",
    icon: "text-orange-500",
  },
  critical: {
    bg: "bg-red-500/15 border-red-500/30",
    icon: "text-red-500",
  },
};

function openUrl(url: string): Promise<void> {
  if (typeof window !== "undefined" && window.vectora?.openExternal) {
    return window.vectora.openExternal(url);
  }
  window.open(url, "_blank", "noopener,noreferrer");
  return Promise.resolve();
}

function computeSpec(
  status: ReturnType<typeof useLicenseStatus>["status"],
): BannerSpec {
  if (!status || status.status === "offline") {
    return { severity: null, icon: AlertTriangle, message: "", cta: null };
  }
  // Token deixou de ser obrigatório (é feature Pro/VPS, não requisito de uso
  // local) — sem token configurado não é mais um estado de alerta, é o
  // padrão esperado pra quem roda local/Free.
  if (!status.configured) {
    return { severity: null, icon: AlertTriangle, message: "", cta: null };
  }
  if (status.status === "expired" || status.status === "revoked") {
    return {
      severity: "critical",
      icon: ShieldAlert,
      message: m.license_banner_expired(),
      cta: { label: m.license_banner_renew(), action: "portal" },
    };
  }
  if (status.status === "past_due") {
    return {
      severity: "danger",
      icon: AlertTriangle,
      message: m.license_banner_past_due(),
      cta: { label: m.license_banner_manage(), action: "portal" },
    };
  }
  if (
    (status.status === "trial" || status.status === "trialing") &&
    status.days_remaining <= TRIAL_WARN_DAYS
  ) {
    return {
      severity: "warning",
      icon: Clock,
      message: m.license_banner_trial_ending({
        n: status.days_remaining,
      }),
      cta: { label: m.license_banner_subscribe(), action: "portal" },
    };
  }
  return { severity: null, icon: AlertTriangle, message: "", cta: null };
}
