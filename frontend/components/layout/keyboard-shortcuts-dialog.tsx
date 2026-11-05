"use client";

/**
 * KeyboardShortcutsDialog — cheatsheet de atalhos gerada do registry C.11.
 *
 * Exibe todos os atalhos registrados no app em categorias, com suporte a
 * i18n (EN/ES/PT) e formatação por plataforma (⌘ no Mac, Ctrl no Windows).
 */

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { isMac } from "@/hooks/useKeyboardShortcuts";
import { m } from "@/lib/paraglide/messages";
import { mDyn } from "@/lib/i18n-dyn";

// ---------------------------------------------------------------------------
// Static shortcut registry (espelha use-global-shortcuts.ts)
// ---------------------------------------------------------------------------

interface ShortcutEntry {
  descKey: string;
  keys: string[];
}

const SHORTCUT_CATEGORIES: Array<{
  labelKey: string;
  shortcuts: ShortcutEntry[];
}> = [
  {
    labelKey: "shortcuts.cat_navigation",
    shortcuts: [
      { descKey: "shortcuts.new_chat", keys: ["Ctrl", "T"] },
      { descKey: "shortcuts.open_settings", keys: ["Ctrl", ","] },
      { descKey: "shortcuts.command_palette", keys: ["Ctrl", "K"] },
      { descKey: "shortcuts.keyboard_shortcuts", keys: ["Ctrl", "?"] },
    ],
  },
  {
    labelKey: "shortcuts.cat_chat",
    shortcuts: [
      { descKey: "shortcuts.clear_messages", keys: ["Ctrl", "L"] },
      { descKey: "shortcuts.focus_input", keys: ["Shift", "E"] },
      { descKey: "shortcuts.scroll_bottom", keys: ["Shift", "G"] },
    ],
  },
  {
    labelKey: "shortcuts.cat_workbench",
    shortcuts: [
      { descKey: "shortcuts.toggle_workbench", keys: ["Ctrl", "\\"] },
    ],
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MOD = isMac() ? "⌘" : "Ctrl";

function formatKeys(keys: string[]): string {
  return keys.map((k) => (k === "Ctrl" ? MOD : k)).join(isMac() ? "" : "+");
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface KeyboardShortcutsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function KeyboardShortcutsDialog({
  open,
  onOpenChange,
}: KeyboardShortcutsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-xl">{m.shortcuts_title()}</DialogTitle>
          <DialogDescription className="sr-only">
            {m.shortcuts_title()}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 py-2">
          {SHORTCUT_CATEGORIES.map(({ labelKey, shortcuts }) => (
            <div key={labelKey}>
              <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3 pb-1.5 border-b border-border/50">
                {mDyn(labelKey)}
              </h3>
              <div className="space-y-1.5">
                {shortcuts.map(({ descKey, keys }) => (
                  <div
                    key={descKey}
                    className="flex items-center justify-between py-2.5 px-3 rounded-lg bg-muted/30 hover:bg-muted/50 transition-colors border border-border/20"
                  >
                    <span className="text-sm text-foreground">
                      {mDyn(descKey)}
                    </span>
                    <kbd className="inline-flex items-center rounded-md border border-border bg-card px-2.5 py-1 font-mono text-xs font-semibold text-foreground shadow-sm">
                      {formatKeys(keys)}
                    </kbd>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
