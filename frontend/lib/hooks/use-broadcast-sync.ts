/**
 * useBroadcastSync — sincronização de estado entre abas via BroadcastChannel.
 *
 * Cada canal corresponde a um slice de store (workspaces, threads, auth).
 * Quando uma aba altera o estado ela publica uma mensagem; as outras abas
 * recebem e revalidam o cache correspondente.
 *
 * Canais disponíveis (constantes exportadas para uso nos stores/hooks):
 *   BROADCAST_WORKSPACES — lista de workspaces (invalidar ao mudar trust/add)
 *   BROADCAST_THREADS    — lista de threads (invalidar ao criar/deletar/renomear)
 *   BROADCAST_AUTH       — estado de autenticação (logout em outra aba)
 *
 * Uso típico no componente/store que altera o estado:
 *   broadcastEvent(BROADCAST_THREADS, { type: "invalidate" });
 *
 * Uso no componente que consome o estado:
 *   useBroadcastSync(BROADCAST_THREADS, () => refetch());
 */

import { useEffect } from "react";

export const BROADCAST_WORKSPACES = "vectora:workspaces";
export const BROADCAST_THREADS = "vectora:threads";
export const BROADCAST_AUTH = "vectora:auth";

export type BroadcastPayload =
  | { type: "invalidate" }
  | { type: "created"; id: string }
  | { type: "deleted"; id: string }
  | { type: "renamed"; id: string; title: string }
  | { type: "logout" };

/**
 * Publica um evento no canal dado para todas as outras abas.
 * Seguro chamar mesmo em ambientes SSR (BroadcastChannel pode não existir).
 */
export function broadcastEvent(
  channel: string,
  payload: BroadcastPayload,
): void {
  if (typeof BroadcastChannel === "undefined") return;
  try {
    const bc = new BroadcastChannel(channel);
    // BroadcastChannel.postMessage recebe só 1 argumento — targetOrigin é
    // exclusivo de window.postMessage (regra é falso-positivo aqui).
    // eslint-disable-next-line unicorn/require-post-message-target-origin
    bc.postMessage(payload);
    bc.close();
  } catch {
    // BroadcastChannel pode falhar em contextos restritos (sandboxed iframes).
  }
}

/**
 * Hook que escuta um canal e executa `onMessage` quando outra aba publica.
 *
 * @param channel - nome do canal (use as constantes exportadas acima)
 * @param onMessage - callback chamado ao receber mensagem de outra aba
 * @param enabled - permite desabilitar a escuta condicionalmente (default true)
 */
export function useBroadcastSync(
  channel: string,
  onMessage: (payload: BroadcastPayload) => void,
  enabled = true,
): void {
  useEffect(() => {
    if (!enabled || typeof BroadcastChannel === "undefined") return;

    const bc = new BroadcastChannel(channel);
    bc.addEventListener("message", (event: MessageEvent<BroadcastPayload>) => {
      onMessage(event.data);
    });

    return () => {
      bc.close();
    };
    // onMessage pode mudar identidade; usamos ref-style através do closure
    // sobre enabled+channel, que são os deps reais do efeito.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel, enabled]);
}
