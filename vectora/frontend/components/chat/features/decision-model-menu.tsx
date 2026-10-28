"use client";

import { useState } from "react";
import { Check, ChevronDown, ShieldCheck } from "lucide-react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  useSettingsStore,
  type DecisionModelProvider,
} from "@/lib/stores/settings-store";
import { m } from "@/lib/paraglide/messages";

const PROVIDERS: Array<{ value: DecisionModelProvider; label: string }> = [
  { value: "clef", label: "Clef" },
  { value: "laya", label: "Laya" },
  { value: "jev", label: "JEV" },
];

/**
 * Compact decision-model control used beside effort and chat model controls.
 * This is a preference surface only: the backend remains responsible for
 * readiness, authorization and the actual decision made for a run.
 */
export function DecisionModelMenu({ compact = false }: { compact?: boolean }) {
  const enabled = useSettingsStore((state) => state.decisionModelsEnabled);
  const provider = useSettingsStore((state) => state.decisionModelProvider);
  const setEnabled = useSettingsStore(
    (state) => state.setDecisionModelsEnabled,
  );
  const setProvider = useSettingsStore(
    (state) => state.setDecisionModelProvider,
  );
  const [open, setOpen] = useState(false);
  const selected = PROVIDERS.find((item) => item.value === provider);
  const title = enabled
    ? `Decision model: ${selected?.label ?? provider}`
    : m.decision_models_disabled();

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
