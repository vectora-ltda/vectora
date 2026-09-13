"use client";
/* oxlint-disable react/set-state-in-effect -- synchronize layout mode with persisted shell state. */

/**
 * IdeModeLayout — arranjo dos painéis do modo IDE (nav-bar do workbench,
 * conteúdo do workbench, editor e chat).
 *
 * Acima do breakpoint `md`, os quatro painéis renderizam lado a lado
 * (comportamento inalterado — o caller já cuida de largura/resize de cada
 * um). Abaixo de `md`, múltiplos painéis lado a lado não cabem na tela:
 * este componente colapsa para mostrar só o painel ativo por vez, com uma
 * faixa de abas no topo para trocar entre eles — o mesmo padrão de "nav
 * strip + view única montada" que `WorkbenchNavBar`/`WorkbenchContent` já
 * usam para as sub-abas (Arquivos/Diff/Plano/Terminal/etc), aplicado aqui
 * ao nível dos três painéis de topo (Chat/Workbench/Editor).
 */

import { useEffect, useState, type ReactNode } from "react";
import { MessageSquare, PanelsTopLeft, Code2 } from "lucide-react";
import { mDyn } from "@/lib/i18n-dyn";

export type IdeMobilePanel = "chat" | "workbench" | "editor";

const MOBILE_TABS: { id: IdeMobilePanel; icon: typeof MessageSquare }[] = [
  { id: "chat", icon: MessageSquare },
  { id: "workbench", icon: PanelsTopLeft },
  { id: "editor", icon: Code2 },
];

interface IdeModeLayoutProps {
  /** true abaixo do breakpoint `md` — só um painel visível por vez. */
  isNarrow: boolean;
  /** Header do app — vive na coluna central (navBar+workbenchContent+editor),
   * nunca em cima do `chat` (que no modo IDE é a coluna lateral direita). */
  header: ReactNode;
  /** Faixa de ícones das sub-abas do workbench (Arquivos/Diff/Plano/etc). */
  navBar: ReactNode;
  /** Conteúdo da sub-aba ativa do workbench, ou `null` quando o painel está fechado. */
  workbenchContent: ReactNode | null;
  editor: ReactNode;
  chat: ReactNode;
  /** Painel inicial em viewport estreita. Default: "editor". */
  defaultMobilePanel?: IdeMobilePanel;
  /** Estado de visibilidade da workbench, usado para evitar selecionar painel fechado. */
  workbenchOpen?: boolean;
}

export function IdeModeLayout({
  isNarrow,
  header,
  navBar,
  workbenchContent,
  editor,
  chat,
  defaultMobilePanel = "editor",
  workbenchOpen = true,
}: IdeModeLayoutProps) {
  const [mobilePanel, setMobilePanel] =
    useState<IdeMobilePanel>(defaultMobilePanel);

  useEffect(() => {
    if (isNarrow && !workbenchOpen && mobilePanel === "workbench") {
      setMobilePanel("editor");
    }
  }, [isNarrow, mobilePanel, workbenchOpen]);

  if (!isNarrow) {
    return (
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Coluna central: header + workbench(navBar/content) + editor —
            `chat` é a coluna lateral direita, fora do header. */}
        <div className="flex flex-col flex-1 min-w-0 min-h-0 overflow-hidden">
          {header}
          <div className="flex flex-1 min-h-0 overflow-hidden">
            {navBar}
            {workbenchContent}
            {editor}
          </div>
        </div>
        {chat}
      </div>
    );
  }

  return (
    <div
      className="flex flex-col flex-1 min-w-0 min-h-0 overflow-hidden"
      data-testid="ide-mobile-layout"
    >
      {header}
      <div
        role="tablist"
        aria-label={mDyn("ide.mobile.tab.workbench")}
        className="flex shrink-0 items-center justify-center gap-1 border-b border-border/60 bg-sidebar px-2 py-1.5"
      >
        {MOBILE_TABS.map(({ id, icon: Icon }) => {
          const active = mobilePanel === id;
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={active}
              data-testid={`ide-mobile-tab-${id}`}
              onClick={() => setMobilePanel(id)}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors ${
                active
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
              }`}
            >
              <Icon className="w-4 h-4" />
              {mDyn(`ide.mobile.tab.${id}`)}
            </button>
          );
        })}
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        {mobilePanel === "chat" && chat}
        {mobilePanel === "workbench" && (
          <div className="flex h-full">
            {navBar}
            {workbenchContent}
          </div>
        )}
        {mobilePanel === "editor" && editor}
      </div>
    </div>
  );
}
