"use client";

import { Menu } from "lucide-react";

import { ContextualHelp } from "./contextual-help";
import { SettingsMenu } from "./settings-menu";
import { ModeSwitch } from "./mode-switcher";
import { m } from "@/lib/paraglide/messages";
import { useElementWidth } from "@/lib/hooks/use-element-width";

interface HeaderProps {
  showToolCalls?: boolean;
  onToggleToolCalls?: () => void;
  onShowShortcuts?: () => void;
  onOpenSidebar?: () => void;
  // O seletor de modo é renderizado uma vez no header de largura total e fica
  // ausente no chatMode, que não oferece os três modos de workbench.
  showModeSwitch?: boolean;
}

export function Header({
  onShowShortcuts,
  onOpenSidebar,
  showModeSwitch,
}: HeaderProps) {
  const [rowRef, rowWidth] = useElementWidth<HTMLDivElement>();

  return (
    <header
      ref={rowRef}
      // h-16: mesma altura fixa da sidebar de sessões (sidebar-header.tsx) e
      // do header do workbench (workbench-panel.tsx) — as 3 colunas do
      // layout precisam da mesma linha divisória de topo, senão a borda
      // horizontal desalinha entre elas.
      className="safe-area-top-header border-b border-border/60 bg-background min-h-[var(--app-header-height)] flex items-center"
    >
      <div className="flex items-stretch justify-between w-full min-w-0 px-4 sm:px-6 self-stretch">
        <div className="flex items-center gap-2 shrink-0">
          {/* Hamburger só em mobile — reabre o sidebar como overlay. */}
          {onOpenSidebar && (
            <button
              type="button"
              onClick={onOpenSidebar}
              aria-label={m.sidebar_open()}
              className="md:hidden -ml-1 mr-1 inline-flex items-center justify-center w-10 h-10 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
            >
              <Menu className="w-5 h-5" />
            </button>
          )}
        </div>

        {showModeSwitch && (
          <div className="flex-1 flex items-stretch justify-center min-w-0 self-stretch">
            <ModeSwitch show width={rowWidth} />
          </div>
        )}

        <div className="flex items-center gap-3 shrink-0">
          <ContextualHelp onShowShortcuts={onShowShortcuts} />
          <SettingsMenu />
        </div>
      </div>
    </header>
  );
}
