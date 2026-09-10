"use client";

import { useEffect, useState } from "react";

import { formatTokens, usageBarColor, usageLevel } from "@/lib/utils/usage";
import {
  getContextWindow,
  type ModelOption,
} from "@/lib/config/deployment-config";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { m } from "@/lib/paraglide/messages";

interface ProviderUsage {
  provider: string;
  label: string;
  used: number | null;
  limit: number | null;
  remaining: number | null;
  plan: string | null;
  unit: string;
  error: string | null;
}

interface MediaQuota {
  period: string;
  used: number;
  limit: number;
  remaining: number;
}

function formatAmount(value: number, unit: string): string {
  return unit === "usd"
    ? `$${value.toFixed(2)}`
    : value.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function ProviderRow({ item }: { item: ProviderUsage }) {
  // Falha aparece como falha: mostrar 0 faria o usuário achar que não gastou
  // nada quando a consulta é que não respondeu.
  if (item.error) {
    return (
      <div className="space-y-0.5">
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">{item.label}</span>
          <span className="text-destructive text-[11px]">
            {m.meter_provider_error()}
          </span>
        </div>
        <p className="text-[10px] text-muted-foreground truncate">
          {item.error}
        </p>
      </div>
    );
  }

  const pct =
    item.limit && item.limit > 0 && item.used !== null
      ? (item.used / item.limit) * 100
      : 0;

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">
          {item.label}
          {item.plan ? ` · ${item.plan}` : ""}
        </span>
        <span className="font-mono text-foreground/90">
          {item.used !== null ? formatAmount(item.used, item.unit) : "—"}
          {item.limit !== null
            ? ` / ${formatAmount(item.limit, item.unit)}`
            : ""}
        </span>
      </div>
      {item.limit !== null && <UsageBar pct={pct} />}
    </div>
  );
}

interface UsagePopoverProps {
  tokensUsed: number;
  modelId: string;
}

function UsageBar({ pct }: { pct: number }) {
  const color = usageBarColor(usageLevel(pct));
  return (
    <div className="h-1 rounded-full bg-muted/60 overflow-hidden">
      <div
        className={`h-full ${color} transition-all duration-300`}
        style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
      />
    </div>
  );
}

const RING_RADIUS = 8;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

function UsageRing({ pct }: { pct: number }) {
  const clamped = Math.min(100, Math.max(0, pct));
  const offset = RING_CIRCUMFERENCE * (1 - clamped / 100);
  // Anel de progresso do uso da janela de contexto: sempre azul; o nível
  // (verde/âmbar/vermelho) aparece na barra dentro do popover.
  return (
    <svg viewBox="0 0 20 20" className="h-3.5 w-3.5 -rotate-90" aria-hidden>
      <circle
        cx="10"
        cy="10"
        r={RING_RADIUS}
        fill="none"
        strokeWidth="2.5"
        stroke="currentColor"
        className="text-muted-foreground/20"
      />
      <circle
        cx="10"
        cy="10"
        r={RING_RADIUS}
        fill="none"
        strokeWidth="2.5"
        strokeLinecap="round"
        stroke="currentColor"
        strokeDasharray={RING_CIRCUMFERENCE}
        strokeDashoffset={offset}
        className="text-blue-500 transition-all duration-300"
      />
    </svg>
  );
}

export function UsagePopover({ tokensUsed, modelId }: UsagePopoverProps) {
  const contextWindow = getContextWindow(modelId as ModelOption);
  const pct = contextWindow > 0 ? (tokensUsed / contextWindow) * 100 : 0;
  const [open, setOpen] = useState(false);
  const [providers, setProviders] = useState<ProviderUsage[]>([]);
  const [mediaQuota, setMediaQuota] = useState<MediaQuota | null>(null);

  // Só busca ao abrir: o consumo dos providers é dado remoto, e o backend
  // cacheia — não faz sentido consultar a cada render do composer.
  useEffect(() => {
    if (!open) return;
    let cancelado = false;
    void fetch("/usage/providers", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { providers: [] }))
      .then((data) => {
        if (!cancelado) setProviders(data.providers ?? []);
      })
      .catch(() => {
        // Sem consumo remoto o popover ainda mostra a janela de contexto.
      });
    setMediaQuota(null);
    void fetch("/usage/media", { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelado && data) setMediaQuota(data as MediaQuota);
        else if (!cancelado) setMediaQuota(null);
      })
      .catch(() => {
        if (!cancelado) setMediaQuota(null);
      });
    return () => {
      cancelado = true;
    };
  }, [open]);

  const valueLabel = `${formatTokens(tokensUsed)} / ${formatTokens(
    contextWindow,
  )} (${pct.toFixed(0)}%)`;

  return (
    <div className="relative">
      <TooltipProvider delayDuration={200}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="flex items-center justify-center p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/40 transition-colors"
              aria-haspopup="dialog"
              aria-expanded={open}
              aria-label={`${m.meter_context_window()}: ${valueLabel}`}
            >
              <UsageRing pct={pct} />
            </button>
          </TooltipTrigger>
          <TooltipContent side="top" className="z-[100] font-mono text-xs">
            {valueLabel}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

      {open && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <div
            role="dialog"
            className="absolute right-0 bottom-full mb-2 z-50 w-72 rounded-lg border border-border/60 bg-background shadow-xl p-3 space-y-2"
          >
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">
                {m.meter_context_window()}
              </span>
              <span className="font-mono text-foreground/90">
                {formatTokens(tokensUsed)} / {formatTokens(contextWindow)} (
                {pct.toFixed(0)}%)
              </span>
            </div>
            <UsageBar pct={pct} />
            <p className="text-[11px] text-muted-foreground pt-1">{modelId}</p>

            {mediaQuota && (
              <div className="space-y-1 pt-2 mt-1 border-t border-border/60">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">
                    {m.meter_media_quota()}
                  </span>
                  <span className="font-mono text-foreground/90">
                    {mediaQuota.used} / {mediaQuota.limit}
                  </span>
                </div>
                <UsageBar
                  pct={
                    mediaQuota.limit > 0
                      ? (mediaQuota.used / mediaQuota.limit) * 100
                      : 0
                  }
                />
                <p className="text-[10px] text-muted-foreground">
                  {m.meter_media_remaining({ count: mediaQuota.remaining })}
                </p>
              </div>
            )}

            {providers.length > 0 && (
              <div className="space-y-2 pt-2 mt-1 border-t border-border/60">
                <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
                  {m.meter_provider_usage()}
                </p>
                {providers.map((item) => (
                  <ProviderRow key={item.provider} item={item} />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
