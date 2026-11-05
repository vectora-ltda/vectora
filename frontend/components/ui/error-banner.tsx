"use client";

/**
 * ErrorBanner — feedback inline padrão para stores com `error: string | null`.
 * Usar onde um estado de carregamento falhou e o usuário precisa de
 * uma ação clara para recuperar — não substitui o toast (que é para eventos
 * pontuais); o banner fica visível enquanto o erro persistir.
 *
 * @example
 *   {status === "error" && (
 *     <ErrorBanner message={error} onRetry={() => void hydrate()} />
 *   )}
 */

import { useState } from "react";
import { AlertCircle, RefreshCw } from "lucide-react";

import { cn } from "@/lib/utils";
import { m } from "@/lib/paraglide/messages";

interface ErrorBannerProps {
  /** Mensagem de erro a exibir (geralmente `store.error`). */
  message: string;
  /** Título opcional — default é `error_banner.title` ("Algo deu errado"). */
  title?: string;
  /** Ação de nova tentativa. Quando ausente, o botão "Tentar novamente" some. */
  onRetry?: () => void | Promise<void>;
  className?: string;
}

export function ErrorBanner({
  message,
  title,
  onRetry,
  className,
}: ErrorBannerProps) {
  const [retrying, setRetrying] = useState(false);

  async function handleRetry() {
    if (!onRetry || retrying) return;
    setRetrying(true);
    try {
      await onRetry();
    } finally {
      setRetrying(false);
    }
  }

  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/10 px-3.5 py-2.5 text-sm text-destructive",
        className,
      )}
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{title ?? m.error_banner_title()}</p>
        <p className="mt-0.5 break-words text-destructive/80">{message}</p>
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={() => void handleRetry()}
          disabled={retrying}
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-destructive/30 px-2.5 py-1 text-xs font-medium transition-colors hover:bg-destructive/15 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <RefreshCw className={cn("h-3 w-3", retrying && "animate-spin")} />
          {retrying ? m.error_banner_retrying() : m.error_banner_retry()}
        </button>
      )}
    </div>
  );
}
