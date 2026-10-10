"use client";

import { ContextualHelp } from "./contextual-help";
import { SettingsMenu } from "./settings-menu";
import { ModeSwitch } from "./mode-switcher";
import { useElementWidth } from "@/lib/hooks/use-element-width";

interface HeaderProps {
  showToolCalls?: boolean;
  onToggleToolCalls?: () => void;
  onShowShortcuts?: () => void;
  // O seletor de modo ocupa o centro do Header quando a tela oferece os três
  // modos. O Header permanece uma única faixa de largura total, independente
  // da coluna de conteúdo ativa, para manter o seletor centralizado.
  showModeSwitch?: boolean;
}

export function Header({ onShowShortcuts, showModeSwitch }: HeaderProps) {
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
        {showModeSwitch && (
          <div className="flex-1 flex justify-center min-w-0">
            <ModeSwitch show width={rowWidth} />
          </div>
        )}

        <div className="ml-auto flex items-center gap-3 shrink-0">
          <ContextualHelp onShowShortcuts={onShowShortcuts} />
          <SettingsMenu />
        </div>
      </div>
    </header>
  );
}
