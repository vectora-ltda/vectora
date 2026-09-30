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
    <div className="flex-1 flex items-center justify-center px-3 sm:px-4">
      <div className="w-full max-w-3xl -mt-6 sm:-mt-20 text-center">
        <div className="mb-4 sm:mb-6 flex items-center justify-center gap-2 sm:gap-4">
          <Image
            src="/vectora.svg"
            alt={m.app_name()}
            width={64}
            height={64}
            priority
            className="h-10 w-10 sm:h-14 sm:w-16"
          />
          <span
            className="text-3xl sm:text-5xl lg:text-6xl font-bold tracking-tight text-primary break-words"
            style={{ fontFamily: "var(--font-aeonik-mono)" }}
          >
            {m.app_name()}
          </span>
        </div>
        <h2
          className="px-2 text-xl sm:text-3xl lg:text-4xl font-semibold text-foreground mb-6 sm:mb-8 break-words"
          style={{ fontFamily: "var(--font-aeonik-mono)" }}
        >
          {m.welcome_title()}
        </h2>

        {onSelect && (
          <div className="flex w-full max-w-xl mx-auto flex-col gap-3">
            {suggestions.map((s) => (
              <button
                key={s}
                onClick={() => onSelect(s)}
                className="w-full px-4 py-3 rounded-xl border border-border/60 bg-muted/30 hover:bg-muted/60 text-sm text-foreground/80 hover:text-foreground transition-colors text-left"
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
