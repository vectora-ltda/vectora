/**
 * FeaturesAccordion — lista de features no estilo changelog: título +
 * resumo sempre visíveis, descrição completa some por trás de um
 * "expandir" (mesmo mecanismo do FaqAccordion, com um ícone por
 * categoria em vez de só texto).
 */

import * as Accordion from "@radix-ui/react-accordion";
import { ChevronDown } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { m } from "#/paraglide/messages";

export interface FeatureItem {
  id: string;
  Icon: LucideIcon;
  title: string;
  summary: string;
  description: string;
  /** Exige o plano Vectora Pro — mostra o badge "PRO" ao lado do título. */
  pro?: boolean;
}

interface FeaturesAccordionProps {
  items: FeatureItem[];
}

export default function FeaturesAccordion({ items }: FeaturesAccordionProps) {
  return (
    <Accordion.Root type="single" collapsible className="space-y-2">
      {items.map((item) => {
        const { Icon } = item;
        return (
          <Accordion.Item
            key={item.id}
            value={item.id}
            className="rounded-lg border border-border bg-card overflow-hidden"
          >
            <Accordion.Header>
              {/* asChild + <div> em vez do <button> nativo do Trigger —
                  browsers não permitem seleção de texto por clique-arraste
                  dentro de um <button>, e título/resumo abaixo precisam ser
                  selecionáveis. Radix só injeta aria-expanded/data-state/
                  onClick via Slot (não role nem comportamento de teclado
                  nativo de <button>) — role="button"/tabIndex/onKeyDown
                  abaixo são explícitos pra manter o mesmo suporte a
                  teclado (Enter/Espaço) e leitor de tela de antes. */}
              <Accordion.Trigger asChild>
                <div
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    // e.repeat: segurar Enter/Espaço dispara keydown repetido
                    // pelo auto-repeat do teclado — sem essa guarda, cada
                    // repetição chamava click() de novo, abrindo/fechando o
                    // accordion várias vezes enquanto a tecla ficava presa.
                    if ((e.key === "Enter" || e.key === " ") && !e.repeat) {
                      e.preventDefault();
                      e.currentTarget.click();
                    }
                  }}
                  className="group flex w-full cursor-pointer items-start gap-3 px-5 py-4 text-start transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary data-[state=open]:text-primary"
                >
                  <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                    <Icon className="h-4 w-4 text-primary" />
                  </span>
                  <span className="flex-1 min-w-0">
                    <span className="flex items-center gap-2">
                      <span className="block text-sm font-medium text-foreground group-data-[state=open]:text-primary">
                        {item.title}
                      </span>
                      {item.pro && (
                        <span className="rounded-full border border-accent-amber/30 bg-accent-amber/10 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-accent-amber">
                          {m.feature_badge_pro()}
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 block text-sm text-muted-foreground">
                      {item.summary}
                    </span>
                  </span>
                  <ChevronDown
                    size={16}
                    className="mt-1.5 shrink-0 text-muted-foreground transition-transform duration-200 group-data-[state=open]:rotate-180"
                  />
                </div>
              </Accordion.Trigger>
            </Accordion.Header>
            <Accordion.Content className="overflow-hidden data-[state=closed]:animate-[slideUp_0.2s_ease] data-[state=open]:animate-[slideDown_0.2s_ease]">
              <div className="border-t border-border px-5 py-4 ps-[3.75rem] text-sm text-muted-foreground leading-relaxed">
                {item.description}
              </div>
            </Accordion.Content>
          </Accordion.Item>
        );
      })}
    </Accordion.Root>
  );
}
