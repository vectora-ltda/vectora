"use client";

import Image from "next/image";
import { Menu } from "lucide-react";

import { ContextualHelp } from "./contextual-help";
import { SettingsMenu } from "./settings-menu";
import { ModeSwitch } from "./mode-switcher";
import { m } from "@/lib/paraglide/messages";
import { useIsDesktop } from "@/lib/hooks/use-is-desktop";
import { useElementWidth } from "@/lib/hooks/use-element-width";

interface HeaderProps {
  showToolCalls?: boolean;
  onToggleToolCalls?: () => void;
  onShowShortcuts?: () => void;
  onOpenSidebar?: () => void;
  //: Mostra o seletor Assistente/IDE/Kanban centralizado nesta mesma barra
  //: (ausente em chatMode, que não tem os 3 modos). Antes vivia numa linha
  //: separada acima do Header — o usuário via duas barras empilhadas
  //: (abas de modo + ajuda/config) em vez de uma só. Unificar exige que o
  //: Header seja renderizado UMA vez, com largura cheia, nos 3 modos —
  //: nunca aninhado dentro da coluna do editor/chat/board, que tem largura
  //: diferente por modo (a mesma causa raiz do bug de posição original).
  showModeSwitch?: boolean;
}

export function Header({
  onShowShortcuts,
  onOpenSidebar,
  showModeSwitch,
}: HeaderProps) {
  // No desktop, ícone+título já aparecem na TitleBar (canto superior
  // esquerdo, ao lado de voltar/recarregar) — repeti-los aqui só ocupa
  // altura à toa. Na web (sem TitleBar), continuam aqui como identidade
  // da página.
  const desktop = useIsDesktop();
  const [rowRef, rowWidth] = useElementWidth<HTMLDivElement>();

  return (
    <header
      ref={rowRef}
      // h-16: mesma altura fixa da sidebar de sessões (sidebar-header.tsx) e
      // do header do workbench (workbench-panel.tsx) — as 3 colunas do
      // layout precisam da mesma linha divisória de topo, senão a borda
      // horizontal desalinha entre elas.
      className="safe-area-top border-b border-border/60 bg-background min-h-16 flex items-center"
    >
      <div className="flex items-center justify-between w-full min-w-0 px-4 sm:px-6">
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
          {!desktop && (
            <>
              <Image
                src="/vectora.svg"
                alt={m.app_name()}
                width={28}
                height={28}
                priority
                className="h-7 w-7"
              />
              <span
                className="text-xl font-semibold tracking-tight text-foreground"
                style={{ fontFamily: "var(--font-aeonik-mono)" }}
              >
                {m.app_name()}
              </span>
            </>
          )}
        </div>

        {showModeSwitch && (
          <div className="flex-1 flex justify-center min-w-0">
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
