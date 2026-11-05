"use client";

/**
 * Publica o status combinado de rede do cliente:
 *   - `offline` reflete os eventos `online`/`offline` do navegador.
 *   - `sseReconnecting` reflete `sseStatus === "reconnecting"`, publicado
 *     via `useNetworkStore.setSSEStatus()` pelo consumidor do
 *     `EventSource` quando detecta `onerror`/retry.
 *
 * O hook só publica; quem consome decide se mostra banner, badge no
 * header ou desabilita botões. O estado SSE vive numa store leve (sem
 * persist) para permitir que qualquer componente reporte sem propagar
 * contexto React.
 */

import { useEffect, useState } from "react";
import { create } from "zustand";

type SSEStatus = "idle" | "connected" | "reconnecting" | "failed";

interface NetworkState {
  sseStatus: SSEStatus;
  setSSEStatus: (status: SSEStatus) => void;
}

export const useNetworkStore = create<NetworkState>()((set) => ({
  sseStatus: "idle",
  setSSEStatus: (sseStatus) => set({ sseStatus }),
}));

export interface NetworkStatus {
  /** `true` quando o navegador reporta `offline` ou `navigator.onLine === false`. */
  offline: boolean;
  /** `true` quando alguma conexão SSE está tentando reconectar. */
  sseReconnecting: boolean;
  /** Status SSE explícito para componentes que precisam diferenciar. */
  sseStatus: SSEStatus;
}

export function useNetworkStatus(): NetworkStatus {
  const sseStatus = useNetworkStore((s) => s.sseStatus);
  const [offline, setOffline] = useState<boolean>(() => {
    if (typeof navigator === "undefined") return false;
    return !navigator.onLine;
  });

  useEffect(() => {
    const onOnline = () => setOffline(false);
    const onOffline = () => setOffline(true);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  return {
    offline,
    sseReconnecting: sseStatus === "reconnecting",
    sseStatus,
  };
}
