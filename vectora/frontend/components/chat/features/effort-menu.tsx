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

export function EffortMenu() {
  const effort = useSettingsStore((s) => s.reasoningEffort);
  const setEffort = useSettingsStore((s) => s.setReasoningEffort);
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          className="flex items-center gap-1.5 min-w-0 px-2.5 py-1.5 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors select-none"
          title={msg.effort_title()}
          aria-label={`${msg.effort_title()}: ${mDyn(`effort.${effort}`)}`}
          aria-expanded={open}
        >
          <span className="hidden truncate font-medium @sm/composer:inline">
            {mDyn(`effort.${effort}`)}
          </span>
          <ChevronDown className="w-3 h-3 shrink-0" />
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
