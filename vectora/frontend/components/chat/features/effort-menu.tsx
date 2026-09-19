"use client";

/**
 * EffortMenu — seletor de esforço de raciocínio na appbar.
 *
 * Mostra o nível atual (reasoningEffort) e a lista de níveis; a escolha
 * persiste no settings-store e vai em cada request como config.reasoning_effort.
 * Usa Popover com portal para escapar da stacking context do composer — um
 * `absolute` comum renderiza atrás da sidebar.
 */

import { useState } from "react";
import { Check, ChevronDown } from "lucide-react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  useSettingsStore,
  REASONING_EFFORTS,
} from "@/lib/stores/settings-store";
import { m as msg } from "@/lib/paraglide/messages";
import { mDyn } from "@/lib/i18n-dyn";

export function EffortMenu({ compact = false }: { compact?: boolean }) {
  const effort = useSettingsStore((s) => s.reasoningEffort);
  const setEffort = useSettingsStore((s) => s.setReasoningEffort);
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          className={`flex min-w-0 items-center rounded-md text-xs text-muted-foreground transition-colors select-none hover:bg-muted/50 hover:text-foreground ${compact ? "h-7 min-w-0 gap-0.5 px-1" : "gap-1.5 px-2.5 py-1.5"}`}
          title={msg.effort_title()}
          aria-label={`${msg.effort_title()}: ${mDyn(`effort.${effort}`)}`}
          aria-expanded={open}
        >
          <span
            className={`inline min-w-0 truncate font-medium ${compact ? "max-w-12" : "max-w-14"}`}
          >
            {mDyn(`effort.${effort}`)}
          </span>
          <ChevronDown className="block h-3 w-3 shrink-0" />
        </button>
      </PopoverTrigger>

      <PopoverContent
        align="end"
        side="top"
        sideOffset={6}
        className="z-50 w-32 rounded-lg border border-border bg-background shadow-xl p-0 py-1"
      >
        <div className="px-3 py-2 text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {msg.effort_title()}
        </div>
        {REASONING_EFFORTS.map((e) => (
          <button
            key={e}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-accent text-left transition-colors"
            onClick={() => {
              setEffort(e);
              setOpen(false);
            }}
          >
            {e === effort ? (
              <Check className="w-4 h-4 shrink-0 text-primary" />
            ) : (
              <span className="w-4 h-4 shrink-0" />
            )}
            <span className="truncate font-medium text-foreground">
              {mDyn(`effort.${e}`)}
            </span>
          </button>
        ))}
      </PopoverContent>
    </Popover>
  );
}
