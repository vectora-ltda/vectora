/**
 * defaultErrorComponent do router — cobre qualquer rejeição de loader que
 * nenhuma rota trata explicitamente (ex.: `/session/$threadId` sem token
 * válido após `/auth/refresh` falhar). Sem isso o TanStack Router caía no
 * boundary genérico dele, sem mensagem amigável nem ação de retry —
 * inconsistente com o cuidado que já existe no tratamento de erro do SSE
 * (`streamErrorMessage`, i18n).
 */

import type { ErrorComponentProps } from "@tanstack/react-router";

import { ErrorBanner } from "@/components/ui/error-banner";
import { m } from "@/lib/paraglide/messages";

export function RouteErrorComponent({ error, reset }: ErrorComponentProps) {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="w-full max-w-md">
        <ErrorBanner
          title={m.route_error_title()}
          message={error instanceof Error ? error.message : String(error)}
          onRetry={() => reset()}
        />
      </div>
    </div>
  );
}
