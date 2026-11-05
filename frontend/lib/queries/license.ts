/**
 * TanStack Query hook para status de licença.
 *
 * Substitui o padrão useState+useEffect+setInterval de use-license-status.ts.
 * Refetch automático a cada 5 min + on-focus, sem gerenciar timers manualmente.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import type { LicenseStatus } from "@/lib/hooks/use-license-status";

export type {
  LicenseStatus,
  LicenseTier,
  LicenseState,
} from "@/lib/hooks/use-license-status";

export const licenseQueryKey = ["license", "status"] as const;

const REVALIDATE_MS = 5 * 60 * 1000;

async function fetchLicenseStatus(): Promise<LicenseStatus> {
  const res = await fetch("/license/status", {
    headers: { Accept: "application/json" },
  });
  if (!res.ok && res.status !== 503) {
    throw new Error(`license/status: ${res.status}`);
  }
  return res.json() as Promise<LicenseStatus>;
}

/** Status de licença do servidor com cache reativo. */
export function useLicenseQuery(): UseQueryResult<LicenseStatus, Error> {
  return useQuery({
    queryKey: licenseQueryKey,
    queryFn: fetchLicenseStatus,
    staleTime: REVALIDATE_MS,
    refetchInterval: REVALIDATE_MS,
    refetchOnWindowFocus: true,
    retry: false,
  });
}
