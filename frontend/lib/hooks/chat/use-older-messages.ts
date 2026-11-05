/**
 * useOlderMessages — carrega mensagens antigas via IntersectionObserver.
 *
 * Observa um elemento sentinel no topo da lista. Quando fica visível e há
 * mensagens mais antigas (hasMore=true), faz fetch de 50 mensagens mais
 * antigas e chama onOlderLoaded com o resultado.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  HistoryMessage,
  PagedHistoryResponse,
} from "@/lib/api/vectora-client";
import { getHistoryPage } from "@/lib/api/vectora-client";

const PAGE_SIZE = 50;

export interface UseOlderMessagesResult {
  hasMore: boolean;
  isLoadingOlder: boolean;
  olderMessages: HistoryMessage[];
}

/**
 * @param threadId  ID da thread
 * @param sentinelRef  ref para o elemento sentinel no topo da lista
 * @param currentCount  número de mensagens atualmente carregadas (para calcular offset)
 * @param initialHasMore  se há mensagens mais antigas (do GetHistory inicial)
 */
export function useOlderMessages(
  threadId: string,
  sentinelRef: React.RefObject<Element | null>,
  currentCount: number,
  initialHasMore = false,
): UseOlderMessagesResult {
  const [hasMore, setHasMore] = useState(initialHasMore);
  const [isLoadingOlder, setIsLoadingOlder] = useState(false);
  const [olderMessages, setOlderMessages] = useState<HistoryMessage[]>([]);
  const isLoadingRef = useRef(false);

  // Ajusta o estado de paginação a partir das props durante o render (não
  // num efeito): ao trocar de thread, zera olderMessages e adota o hasMore
  // inicial da nova thread; ao só mudar initialHasMore (ex.: após o
  // GetHistory inicial resolver) sincroniza hasMore sem mexer no restante.
  const [prevThreadId, setPrevThreadId] = useState(threadId);
  const [prevInitialHasMore, setPrevInitialHasMore] = useState(initialHasMore);
  if (threadId !== prevThreadId) {
    setPrevThreadId(threadId);
    setPrevInitialHasMore(initialHasMore);
    setHasMore(initialHasMore);
    setOlderMessages([]);
  } else if (initialHasMore !== prevInitialHasMore) {
    setPrevInitialHasMore(initialHasMore);
    setHasMore(initialHasMore);
  }

  const loadOlder = useCallback(async () => {
    if (!hasMore || isLoadingRef.current) return;

    isLoadingRef.current = true;
    setIsLoadingOlder(true);

    try {
      // olderMessages.length já é quantas mensagens antigas foram
      // acumuladas até aqui — soma direto ao offset, sem contador à parte.
      const offset = olderMessages.length + currentCount;
      const page: PagedHistoryResponse = await getHistoryPage(
        threadId,
        PAGE_SIZE,
        offset,
      );
      setHasMore(page.has_more);
      setOlderMessages((prev) => [...page.messages, ...prev]);
    } catch {
      // falha silenciosa — usuário pode rolar de novo para tentar
    } finally {
      isLoadingRef.current = false;
      setIsLoadingOlder(false);
    }
  }, [threadId, hasMore, currentCount, olderMessages.length]);

  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !hasMore) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          loadOlder();
        }
      },
      { threshold: 0.1 },
    );

    observer.observe(el);
    return () => observer.disconnect();
  }, [sentinelRef, hasMore, loadOlder]);

  return { hasMore, isLoadingOlder, olderMessages };
}
