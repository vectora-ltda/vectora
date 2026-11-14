/**
 * TanStack Query hook para status de licença.
 *
 * É a fonte do status de licença; `useLicenseStatus` é só a casca com o
 * formato antigo (`status`, `loading`, `refetch`) para quem já o consome.
 * Refetch automático a cada 5 min + on-focus, sem gerenciar timers manualmente.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

export type LicenseTier = "plus" | "pro" | null;
export type LicenseState =
  | "active"
  | "trial"
  | "trialing"
  | "past_due"
  | "expired"
  | "revoked"
  | "unknown"
  | "offline";

export interface LicenseStatus {
  configured: boolean;
  tier: LicenseTier;
  status: LicenseState;
  days_remaining: number;
  expires_at: string;
  cached: boolean;
}

export const licenseQueryKey = ["license", "status"] as const;

const REVALIDATE_MS = 5 * 60 * 1000;

async function fetchLicenseStatus(): Promise<LicenseStatus> {
  const res = await fetch("/license/status", {
    headers: { Accept: "application/json" },
    credentials: "include",
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
