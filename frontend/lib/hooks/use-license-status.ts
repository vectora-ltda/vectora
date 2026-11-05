"use client";

/**
 * Hook que mantém o status de licença do servidor cacheado e revalida em
 * background. Padrão SWR igual ao ``use-usage``: render imediato + refetch
 * a cada 5 min + on-focus + after-login.
 */

import { useCallback, useEffect, useState } from "react";

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

const REVALIDATE_MS = 5 * 60 * 1000;

async function fetchStatus(): Promise<LicenseStatus | null> {
  try {
    const res = await fetch("/license/status", {
      headers: { Accept: "application/json" },
      credentials: "include",
    });
    if (!res.ok && res.status !== 503) return null;
    return (await res.json()) as LicenseStatus;
  } catch {
    return null;
  }
}

export function useLicenseStatus(): {
  status: LicenseStatus | null;
  loading: boolean;
  refetch: () => Promise<void>;
} {
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refetch = useCallback(async () => {
    const data = await fetchStatus();
    if (data) setStatus(data);
    setLoading(false);
  }, []);

  useEffect(() => {
    // Sincroniza com o sistema externo (fetch de rede) — busca inicial do
    // status de licença, não deriva de estado puro.
    // oxlint-disable-next-line react/set-state-in-effect
    void refetch();
    const interval = window.setInterval(() => void refetch(), REVALIDATE_MS);
    const onFocus = () => void refetch();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
    };
  }, [refetch]);

  return { status, loading, refetch };
}
