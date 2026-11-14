"use client";

import { ContextualHelp } from "./contextual-help";
import { SettingsMenu } from "./settings-menu";
import { GitStatusBadge } from "./git-status-badge";
import { ModeSwitch } from "./mode-switcher";
import { useElementWidth } from "@/lib/hooks/use-element-width";
import { MessageSquare, PanelLeftOpen, PanelRightOpen } from "lucide-react";
import { m } from "@/lib/paraglide/messages";

interface CompactNavigation {
  onOpenSessions: () => void;
  workbenchOpen?: boolean;
  onToggleWorkbench?: () => void;
}

interface HeaderProps {
  showToolCalls?: boolean;
  onToggleToolCalls?: () => void;
  onShowShortcuts?: () => void;
  // O seletor de modo ocupa o centro do Header quando a tela oferece os três
  // modos. O Header permanece uma única faixa de largura total, independente
  // da coluna de conteúdo ativa, para manter o seletor centralizado.
  showModeSwitch?: boolean;
  compactNavigation?: CompactNavigation;
}

export function Header({
  onShowShortcuts,
  showModeSwitch,
  compactNavigation,
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
      <div className="flex items-center justify-between w-full min-w-0 px-4 sm:px-6">
        {compactNavigation && (
          <div
            role="group"
            aria-label={m.mobile_shell_controls()}
            className="flex shrink-0 items-center gap-1"
          >
            <button
              type="button"
              aria-label={m.mobile_open_sessions()}
              title={m.mobile_open_sessions()}
              onClick={compactNavigation.onOpenSessions}
              className="flex size-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <PanelLeftOpen className="size-4" aria-hidden="true" />
            </button>
            {compactNavigation.onToggleWorkbench && (
              <button
                type="button"
                aria-label={
                  compactNavigation.workbenchOpen
                    ? m.mobile_show_chat()
                    : m.mobile_show_workbench()
                }
                title={
                  compactNavigation.workbenchOpen
                    ? m.mobile_show_chat()
                    : m.mobile_show_workbench()
                }
                aria-pressed={compactNavigation.workbenchOpen ?? false}
                onClick={compactNavigation.onToggleWorkbench}
                className="flex size-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                {compactNavigation.workbenchOpen ? (
                  <MessageSquare className="size-4" aria-hidden="true" />
                ) : (
                  <PanelRightOpen className="size-4" aria-hidden="true" />
                )}
              </button>
            )}
          </div>
        )}
        {showModeSwitch && (
          <div className="flex-1 flex justify-center min-w-0">
            <ModeSwitch show width={rowWidth} />
          </div>
        )}

        <div className="ml-auto flex items-center gap-3 shrink-0">
          <GitStatusBadge />
          <ContextualHelp onShowShortcuts={onShowShortcuts} />
          <SettingsMenu />
        </div>
      </div>
    </header>
  );
}
