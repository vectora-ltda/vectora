"use client";
/* oxlint-disable react/set-state-in-effect -- synchronize layout mode with persisted shell state. */

/**
 * IdeModeLayout — arranjo dos painéis do modo IDE (nav-bar do workbench,
 * conteúdo do workbench, editor e chat).
 *
 * Em viewport wide, os quatro painéis renderizam lado a lado
 * (comportamento inalterado — o caller já cuida de largura/resize de cada
 * um). Em viewport mobile, múltiplos painéis lado a lado não cabem na tela:
 * este componente colapsa para mostrar só o painel ativo por vez, com uma
 * faixa de abas no topo para trocar entre eles — o mesmo padrão de "nav
 * strip + view única montada" que `WorkbenchNavBar`/`WorkbenchContent` já
 * usam para as sub-abas (Arquivos/Diff/Plano/Terminal/etc), aplicado aqui
 * ao nível dos três painéis de topo (Chat/Workbench/Editor).
 */

import { useEffect, useState, type ReactNode } from "react";
import { MessageSquare, PanelsTopLeft, Code2 } from "lucide-react";
import { useReducedMotion } from "motion/react";
import { mDyn } from "@/lib/i18n-dyn";
import { m } from "@/lib/paraglide/messages";
import { ModeColumnLayout } from "@/components/layout/mode-column-layout";
import { WorkbenchHost } from "@/components/layout/workbench-host";
import type { IdeLayoutState } from "@/lib/hooks/use-media-query";

export type IdeMobilePanel = "chat" | "workbench" | "editor";

const MOBILE_TABS: { id: IdeMobilePanel; icon: typeof MessageSquare }[] = [
  { id: "chat", icon: MessageSquare },
  { id: "workbench", icon: PanelsTopLeft },
  { id: "editor", icon: Code2 },
];

interface IdeModeLayoutProps {
  /** Explicit responsive state. Mobile is reserved for smartphones. */
  layoutState?: IdeLayoutState;
  /** @deprecated use layoutState; kept for callers migrating from the boolean API. */
  isNarrow?: boolean;
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
  /** Reabre a workbench quando ela foi fechada no modo compacto. */
  onOpenWorkbench?: () => void;
  workbenchSide?: "left" | "right";
  direction?: "ltr" | "rtl";
  workbenchWidth?: number;
  workbenchMinWidth?: number;
  workbenchMaxWidth?: number;
  chatWidth?: number;
  chatMinWidth?: number;
  chatMaxWidth?: number;
  /** Keep the chat column mounted while allowing it to collapse to a rail. */
  showChat?: boolean;
  /** Reopens the chat rail after it was collapsed. */
  onOpenChat?: () => void;
}

export function IdeModeLayout({
  layoutState,
  isNarrow = false,
  header,
  navBar,
  workbenchContent,
  editor,
  chat,
  defaultMobilePanel = "editor",
  workbenchOpen = true,
  onOpenWorkbench,
  workbenchSide = "left",
  direction = "ltr",
  workbenchWidth,
  workbenchMinWidth,
  workbenchMaxWidth,
  chatWidth,
  chatMinWidth,
  chatMaxWidth,
  showChat = true,
  onOpenChat,
}: IdeModeLayoutProps) {
  const resolvedLayoutState = layoutState ?? (isNarrow ? "mobile" : "wide");
  const [mobilePanel, setMobilePanel] =
    useState<IdeMobilePanel>(defaultMobilePanel);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (
      resolvedLayoutState === "mobile" &&
      !workbenchOpen &&
      mobilePanel === "workbench"
    ) {
      setMobilePanel("editor");
    }
  }, [resolvedLayoutState, mobilePanel, workbenchOpen]);

  if (resolvedLayoutState !== "mobile") {
    return (
      <ModeColumnLayout
        header={header}
        left={
          <WorkbenchHost
            rail={navBar}
            panel={workbenchContent}
            side={workbenchSide}
          />
        }
        center={editor}
        right={chat}
        direction={direction}
        leftColumn={{
          width: workbenchWidth,
          minWidth: workbenchMinWidth,
          maxWidth: workbenchMaxWidth,
        }}
        rightColumn={{
          width: chatWidth,
          minWidth: showChat ? chatMinWidth : 48,
          maxWidth: chatMaxWidth,
          visibility: showChat ? "visible" : "collapsed",
          onExpand: onOpenChat ?? (() => undefined),
          expandLabel: m.layout_open_chat(),
        }}
      />
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
              onClick={() => {
                if (id === "workbench" && !workbenchOpen) {
                  onOpenWorkbench?.();
                }
                setMobilePanel(id);
              }}
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
      <div className="flex-1 min-h-0 overflow-hidden relative">
        <div
          key={mobilePanel}
          data-motion-disabled={reducedMotion ? "true" : undefined}
          className="absolute inset-0 min-h-0 min-w-0 overflow-hidden transition-opacity duration-150 motion-reduce:transition-none"
        >
          {mobilePanel === "chat" && chat}
          {mobilePanel === "workbench" && (
            <div className="flex h-full min-w-0">
              {navBar}
              {workbenchContent}
            </div>
          )}
          {mobilePanel === "editor" && editor}
        </div>
      </div>
    </div>
  );
}
