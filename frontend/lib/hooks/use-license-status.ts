"use client";

/**
 * Status de licença do servidor, no formato que os consumidores atuais usam
 * (`status`, `loading`, `refetch`). O cache, a revalidação a cada 5 min e o
 * refetch ao voltar o foco vêm de `useLicenseQuery` (TanStack Query).
 */

import { useLicenseQuery } from "@/lib/queries/license";

export type {
  LicenseStatus,
  LicenseTier,
  LicenseState,
} from "@/lib/queries/license";
import type { LicenseStatus } from "@/lib/queries/license";

export function useLicenseStatus(): {
  status: LicenseStatus | null;
  loading: boolean;
  refetch: () => Promise<void>;
} {
  const query = useLicenseQuery();
  return {
    status: query.data ?? null,
    loading: query.isLoading,
    refetch: async () => {
      await query.refetch();
    },
  };
}
