"use client";

import Image from "next/image";
import { useQuery } from "@tanstack/react-query";
import { getStackHint } from "@/lib/api/vectora-client";
import { m } from "@/lib/paraglide/messages";
import { mDyn } from "@/lib/i18n-dyn";

interface EmptyStateHeaderProps {
  /** Chamado quando o usuário clica em uma sugestão — popula o input do chat. */
  onSelect?: (prompt: string) => void;
  /** Workspace ativo, se houver — usado para detectar a stack e adaptar as sugestões. */
  workspaceId?: string;
  /** Renderiza a marca e as sugestões dentro da coluna estreita do IDE. */
  compact?: boolean;
}

/** Stacks conhecidas com 3 sugestões cada. "unknown" é o fallback. */
const KNOWN_STACKS = ["nodejs", "python", "go", "rust", "java"] as const;
type KnownStack = (typeof KNOWN_STACKS)[number] | "unknown";

function isKnownStack(s: string): s is KnownStack {
  return (KNOWN_STACKS as readonly string[]).includes(s) || s === "unknown";
}

/**
 * Cabeçalho exibido acima da lista de mensagens quando a thread ainda
 * está vazia. Mostra logo, título e 3 sugestões clicáveis (CTAs) que
 * populam o input ao ser selecionadas.
 *
 * Quando um workspace está ativo, busca `GET /workspaces/{id}/stack-hint`
 * e usa sugestões específicas da stack detectada.
 */
export function EmptyStateHeader({
  onSelect,
  workspaceId,
  compact = false,
}: EmptyStateHeaderProps) {
  // Busca o stack hint apenas quando há workspace ativo.
  const { data: hintData } = useQuery({
    queryKey: ["stack-hint", workspaceId],
    queryFn: () => getStackHint(workspaceId!),
    enabled: !!workspaceId,
    staleTime: 5 * 60_000,
  });

  const stack: KnownStack =
    hintData && isKnownStack(hintData.stack) ? hintData.stack : "unknown";

  const suggestions = [
    mDyn(`stack.${stack}.1`),
    mDyn(`stack.${stack}.2`),
    mDyn(`stack.${stack}.3`),
  ];

  return (
    <div className="flex-1 flex min-w-0 items-center justify-center px-2 sm:px-4">
      <div
        className={`w-full text-center ${compact ? "max-w-full -mt-2" : "max-w-3xl -mt-6 sm:-mt-20"}`}
      >
        <div
          className={`flex items-center justify-center ${compact ? "mb-4 flex-col gap-2" : "mb-4 gap-2 sm:mb-6 sm:gap-4"}`}
        >
          <Image
            src="/vectora.svg"
            alt={m.app_name()}
            width={64}
            height={64}
            priority
            className={compact ? "h-8 w-8" : "h-10 w-10 sm:h-14 sm:w-16"}
          />
          <span
            className={`max-w-full break-words font-bold tracking-tight text-primary ${compact ? "text-2xl leading-none" : "text-3xl sm:text-5xl lg:text-6xl"}`}
            style={{ fontFamily: "var(--font-aeonik-mono)" }}
          >
            {m.app_name()}
          </span>
        </div>
        <h2
          className={`max-w-full break-words px-2 font-semibold text-foreground ${compact ? "mb-4 text-lg leading-tight" : "mb-6 text-xl sm:mb-8 sm:text-3xl lg:text-4xl"}`}
          style={{ fontFamily: "var(--font-aeonik-mono)" }}
        >
          {m.welcome_title()}
        </h2>

        {onSelect && (
          <div
            className={`mx-auto flex w-full max-w-xl flex-col ${compact ? "gap-2" : "gap-3"}`}
          >
            {suggestions.map((s) => (
              <button
                key={s}
                onClick={() => onSelect(s)}
                className={`w-full rounded-xl border border-border/60 bg-muted/30 text-left text-foreground/80 transition-colors hover:bg-muted/60 hover:text-foreground ${compact ? "px-3 py-2 text-xs leading-snug" : "px-4 py-3 text-sm"}`}
              >
                {s}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
