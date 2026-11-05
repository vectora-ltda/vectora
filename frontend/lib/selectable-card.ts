import { cn } from "@/lib/utils";

export interface SelectableCardState {
  active?: boolean;
  prominent?: boolean;
}

/** Classe de ênfase em 3 níveis para cards clicáveis (grade de temas,
 * resultados de busca no marketplace): `active` (selecionado, anel de
 * destaque), `prominent` (já instalado/configurado, superfície sólida sem
 * anel) e o padrão (transparente até o hover). */
export function selectableCardClass({
  active,
  prominent,
}: SelectableCardState): string {
  return cn(
    "rounded-lg border transition-colors",
    active
      ? "border-primary bg-primary/[0.06] ring-2 ring-primary/20"
      : prominent
        ? "border-border bg-card hover:bg-accent"
        : "border-transparent bg-transparent text-muted-foreground hover:border-border hover:bg-accent",
  );
}
