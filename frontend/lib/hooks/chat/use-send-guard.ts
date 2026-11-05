import { useCallback, useRef } from "react";

/**
 * Guard atômico síncrono contra dupla submissão de envio no chat.
 *
 * `uiState.isLoading` (reducer) só reflete no próximo render do React —
 * duas chamadas ao handler de envio no mesmo tick (Enter rápido duas vezes,
 * Enter + clique no botão) ambas leem `isLoading === false` e duplicariam a
 * mensagem do usuário. O guard usa um `useRef` (mudança síncrona, sem
 * render) para reservar/liberar o envio de forma atômica.
 *
 * Contrato por instância: cada hook tem seu próprio ref, então componentes
 * distintos não compartilham a trava — o ciclo de envio é por-thread.
 */
export function useSendGuard() {
  const busyRef = useRef(false);

  /** Reserva o envio; `false` se já houver um envio em andamento. */
  const tryAcquire = useCallback((): boolean => {
    if (busyRef.current) return false;
    busyRef.current = true;
    return true;
  }, []);

  /** Libera a reserva. Idempotente — chamadas extras são no-op. */
  const release = useCallback((): void => {
    busyRef.current = false;
  }, []);

  return { tryAcquire, release };
}
