"use client";

/**
 * AboutPanel — categoria "Sobre" do SettingsOverlay.
 *
 * Versão do app não é informação administrativa — antes só existia
 * dentro de Administração → Sistema (gated por role admin/root). Usa
 * `GET /health`, rota pública (sem auth), em vez do `/admin/system`
 * gated que alimenta o painel de administração — nunca expõe os
 * diagnósticos internos (contagem de spans, DSN, etc.) que continuam
 * exclusivos de Administração.
 */

import { useEffect, useState } from "react";
import Image from "next/image";
import { Loader2 } from "lucide-react";

import { useIsDesktop } from "@/lib/hooks/use-is-desktop";
import { m } from "@/lib/paraglide/messages";

export function AboutPanel() {
  const [version, setVersion] = useState<string | null>(null);
  const desktop = useIsDesktop();

  useEffect(() => {
    let cancelled = false;
    fetch("/health")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { version?: string } | null) => {
        if (!cancelled && d?.version) setVersion(d.version);
      })
      .catch(() => {
        // Silencioso — versão fica "—", não é informação crítica.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Image src="/vectora.svg" alt="Vectora" width={32} height={32} />
        <span
          className="text-lg font-semibold tracking-tight"
          style={{ fontFamily: "var(--font-aeonik-mono)" }}
        >
          Vectora
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 max-w-xs">
        <div className="rounded-lg border bg-card p-2.5">
          <p className="text-[10px] text-muted-foreground">
            {m.about_panel_version_label()}
          </p>
          <p className="text-xs font-medium font-mono">
            {version ?? <Loader2 className="w-3 h-3 animate-spin" />}
          </p>
        </div>
        <div className="rounded-lg border bg-card p-2.5">
          <p className="text-[10px] text-muted-foreground">
            {m.about_panel_mode_label()}
          </p>
          <p className="text-xs font-medium">
            {desktop ? m.about_panel_mode_desktop() : m.about_panel_mode_web()}
          </p>
        </div>
      </div>
    </div>
  );
}
