"use client";

/**
 * PermissionModeMenu (R2)
 *
 * Chip + dropdown com os 5 modos de permissão. A escolha persiste no
 * settings-store (campo permissionMode) e é enviada em cada request como
 * config.permission_mode, consumida pelo hitl_check no backend.
 *
 * Popover com portal (mesmo padrão do ModelSelector, ver model-selector.tsx)
 * — escapa da stacking context do composer; um `absolute` comum renderizava
 * atrás da sidebar mesmo com z-index nominal maior, porque um ancestral do
 * composer forma sua própria stacking context.
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
  PERMISSION_MODES,
} from "@/lib/stores/settings-store";
import { m as msg } from "@/lib/paraglide/messages";
import { mDyn } from "@/lib/i18n-dyn";

export function PermissionModeMenu({ compact = false }: { compact?: boolean }) {
  const mode = useSettingsStore((s) => s.permissionMode);
  const setMode = useSettingsStore((s) => s.setPermissionMode);
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          className={`flex min-w-0 max-w-full shrink items-center overflow-hidden rounded-md text-xs text-muted-foreground transition-colors select-none hover:bg-muted/50 hover:text-foreground ${compact ? "h-7 gap-0.5 px-1" : "gap-1.5 px-2 py-1.5"}`}
          title={msg.permission_title()}
          aria-label={`${msg.permission_title()}: ${mDyn(`permission.mode.${mode}`)}`}
          aria-expanded={open}
        >
          <span
            className={`inline min-w-0 truncate font-medium ${compact ? "max-w-14" : "max-w-20"}`}
          >
            {mDyn(`permission.mode.${mode}`)}
          </span>
          <ChevronDown className="block h-3 w-3 shrink-0" />
        </button>
      </PopoverTrigger>

      <PopoverContent
        align="end"
        side="top"
        sideOffset={6}
        className="z-50 w-[min(14rem,calc(100vw-1rem))] max-w-[calc(100vw-1rem)] rounded-lg border border-border bg-background shadow-xl p-0 py-1"
      >
        <div className="px-3 py-2 text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {msg.permission_title()}
        </div>
        {PERMISSION_MODES.map((m) => (
          <button
            key={m}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-accent text-left transition-colors"
            onClick={() => {
              setMode(m);
              setOpen(false);
            }}
          >
            {m === mode ? (
              <Check className="w-4 h-4 shrink-0 text-primary" />
            ) : (
              <span className="w-4 h-4 shrink-0" />
            )}
            <span className="truncate font-medium text-foreground">
              {mDyn(`permission.mode.${m}`)}
            </span>
          </button>
        ))}
      </PopoverContent>
    </Popover>
  );
}
