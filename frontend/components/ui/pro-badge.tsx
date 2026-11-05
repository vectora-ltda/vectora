import { m } from "@/lib/paraglide/messages";
import { Badge } from "@/components/ui/badge";
import { useLicenseStatus } from "@/lib/hooks/use-license-status";
import { cn } from "@/lib/utils";

/**
 * Badge "Pro" para sinalizar features gateadas antes do usuário tentar
 * usá-las — nunca some da tela, apenas troca de estilo quando a instalação
 * já é Pro (o objetivo é comunicar preço, não apenas bloqueio).
 */
export function ProBadge({ className }: { className?: string }) {
  const { status, loading } = useLicenseStatus();
  const isPro = !loading && status?.configured && status?.tier === "pro";

  return (
    <Badge
      variant={isPro ? "secondary" : "default"}
      className={cn("text-[9px] h-4 px-1.5 leading-none", className)}
    >
      {m.pro_badge_label()}
    </Badge>
  );
}
