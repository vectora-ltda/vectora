"use client";

import { useEffect, useState } from "react";
import { Check, ChevronDown, ShieldCheck } from "lucide-react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  hydrateDecisionModelPreferences,
  useSettingsStore,
  type DecisionModelProvider,
} from "@/lib/stores/settings-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { m } from "@/lib/paraglide/messages";

const PROVIDERS: Array<{ value: DecisionModelProvider; label: string }> = [
  { value: "clef-local", label: "Clef local" },
  { value: "laya-local", label: "Laya local" },
  { value: "clef-api", label: "Clef Cloudflare" },
  { value: "jev-api", label: "JEV" },
];

/**
 * Compact decision-model control used beside effort and chat model controls.
 * This is a preference surface only: the backend remains responsible for
 * readiness, authorization and the actual decision made for a run.
 */
export function DecisionModelMenu({ compact = false }: { compact?: boolean }) {
  const workspaceId = useWorkspacesStore((state) => state.active_id);
  const enabled = useSettingsStore((state) => state.decisionModelsEnabled);
  const provider = useSettingsStore((state) => state.decisionModelProvider);
  const permissionMode = useSettingsStore((state) => state.permissionMode);
  const setEnabled = useSettingsStore(
    (state) => state.setDecisionModelsEnabled,
  );
  const setProvider = useSettingsStore(
    (state) => state.setDecisionModelProvider,
  );
  const [open, setOpen] = useState(false);
  const [readinessResult, setReadinessResult] = useState<{
    provider: DecisionModelProvider;
    ready: boolean;
    configured: boolean;
    credentialValid: boolean;
    healthy: boolean;
  } | null>(null);
  const [readinessErrorProvider, setReadinessErrorProvider] =
    useState<DecisionModelProvider | null>(null);
  const readinessState =
    readinessResult?.provider === provider ? readinessResult : null;
  const readiness = readinessState?.ready ?? null;
  const readinessError = readinessErrorProvider === provider;
  const selected = PROVIDERS.find((item) => item.value === provider);
  const title = enabled
    ? `Decision model: ${selected?.label ?? provider}`
    : m.decision_models_disabled();

  useEffect(() => {
    void hydrateDecisionModelPreferences(workspaceId);
  }, [workspaceId]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    void fetch(
      `/provider-routing/decision-models/readiness?provider=${encodeURIComponent(provider)}`,
    )
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return (await response.json()) as {
          ready?: boolean;
          configured?: boolean;
          credential_valid?: boolean;
          healthy?: boolean;
        };
      })
      .then((value) => {
        if (active) {
          setReadinessResult({
            provider,
            ready: value.ready === true,
            configured: value.configured === true,
            credentialValid: value.credential_valid === true,
            healthy: value.healthy === true,
          });
        }
      })
      .catch(() => {
        if (active) setReadinessErrorProvider(provider);
      });
    return () => {
      active = false;
    };
  }, [open, provider]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={`flex min-w-0 shrink items-center rounded-md text-xs leading-4 font-medium transition-colors select-none ${
            enabled
              ? "text-foreground hover:bg-muted/70"
              : "text-muted-foreground hover:bg-muted/50"
          } ${compact ? "h-7 w-full gap-0.5 px-1" : "gap-1.5 px-2.5 py-1.5"}`}
          title={title}
          aria-label={title}
          aria-expanded={open}
        >
          <ShieldCheck className={compact ? "size-3" : "size-3.5"} />
          <span className="min-w-0 truncate" data-compact-control-label>
            {enabled ? (selected?.label ?? provider) : "Decision"}
          </span>
          {!compact && <ChevronDown className="size-3 shrink-0" />}
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        side="top"
        sideOffset={6}
        className="z-50 w-56 rounded-lg border border-border bg-background p-0 py-1 shadow-xl"
      >
        <div className="px-3 py-2">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {m.decision_models_title()}
          </div>
          <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
            {m.decision_models_backend_note()}
          </p>
          {permissionMode === "auto" && (
            <p
              role="status"
              className="mt-2 text-[11px] leading-4 text-muted-foreground"
            >
              {!enabled
                ? m.decision_models_readiness_no_guardrail()
                : readinessError
                  ? m.decision_models_readiness_unavailable()
                  : readiness === null
                    ? m.decision_models_readiness_checking()
                    : readiness
                      ? m.decision_models_readiness_ready()
                      : readinessState?.configured === false
                        ? m.decision_models_readiness_unconfigured()
                        : readinessState?.credentialValid === false
                          ? m.decision_models_readiness_no_credential()
                          : readinessState?.healthy === false
                            ? m.decision_models_readiness_unhealthy()
                            : m.decision_models_readiness_no_guardrail()}
            </p>
          )}
        </div>
        <button
          type="button"
          className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-accent"
          onClick={() => setEnabled(!enabled)}
        >
          {enabled ? (
            <Check className="size-4 shrink-0 text-primary" />
          ) : (
            <span className="size-4 shrink-0" />
          )}
          <span className="font-medium text-foreground">
            {m.decision_models_use()}
          </span>
        </button>
        {PROVIDERS.map((item) => (
          <button
            key={item.value}
            type="button"
            disabled={!enabled}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
            onClick={() => {
              setProvider(item.value);
              setOpen(false);
            }}
          >
            {enabled && provider === item.value ? (
              <Check className="size-4 shrink-0 text-primary" />
            ) : (
              <span className="size-4 shrink-0" />
            )}
            <span className="font-medium text-foreground">{item.label}</span>
          </button>
        ))}
      </PopoverContent>
    </Popover>
  );
}
