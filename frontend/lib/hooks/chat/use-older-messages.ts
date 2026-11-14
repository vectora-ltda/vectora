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
import { HistoryCursorGoneError } from "@/lib/api/history-cursor";

const PAGE_SIZE = 50;

export interface UseOlderMessagesResult {
  hasMore: boolean;
  isLoadingOlder: boolean;
  olderMessages: HistoryMessage[];
  /** A última tentativa de carregar a página anterior falhou. */
  loadFailed: boolean;
  /** Pede a página anterior (o observador do topo faz o mesmo ao rolar). */
  loadOlder: () => Promise<void>;
}

/**
 * @param threadId  ID da thread
 * @param sentinelRef  ref para o elemento sentinel no topo da lista
 * @param currentCount  número de mensagens atualmente carregadas (para calcular offset)
 * @param initialHasMore  se há mensagens mais antigas (da página inicial)
 * @param initialCursor  `next_cursor` da página inicial: a mensagem mais antiga
 *   já carregada; com ele a primeira página anterior já pagina por identidade
 * @param resetKey  muda quando a conversa foi recarregada (rewind, branch):
 *   descarta as mensagens anteriores já acumuladas
 */
export function useOlderMessages(
  threadId: string,
  sentinelRef: React.RefObject<Element | null>,
  currentCount: number,
  initialHasMore = false,
  initialCursor: number | null = null,
  resetKey = 0,
): UseOlderMessagesResult {
  const [hasMore, setHasMore] = useState(initialHasMore);
  const [isLoadingOlder, setIsLoadingOlder] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [olderMessages, setOlderMessages] = useState<HistoryMessage[]>([]);
  const isLoadingRef = useRef(false);
  // Cursor da próxima página (a mensagem mais antiga já carregada); só vale
  // para a thread em que foi recebido. Sem ele, a página usa o offset.
  const [cursor, setCursor] = useState<{ threadId: string; id: number } | null>(
    initialCursor == null ? null : { threadId, id: initialCursor },
  );

  // Ajusta o estado de paginação a partir das props durante o render (não
  // num efeito): ao trocar de thread, zera olderMessages e adota o hasMore
  // inicial da nova thread; ao só mudar initialHasMore (ex.: após o
  // GetHistory inicial resolver) sincroniza hasMore sem mexer no restante.
  const [prevThreadId, setPrevThreadId] = useState(threadId);
  const [prevResetKey, setPrevResetKey] = useState(resetKey);
  const [prevInitialHasMore, setPrevInitialHasMore] = useState(initialHasMore);
  const [prevInitialCursor, setPrevInitialCursor] = useState(initialCursor);
  if (threadId !== prevThreadId || resetKey !== prevResetKey) {
    setPrevThreadId(threadId);
    setPrevResetKey(resetKey);
    setPrevInitialHasMore(initialHasMore);
    setPrevInitialCursor(initialCursor);
    setHasMore(initialHasMore);
    setOlderMessages([]);
    setCursor(initialCursor == null ? null : { threadId, id: initialCursor });
  } else {
    if (initialHasMore !== prevInitialHasMore) {
      setPrevInitialHasMore(initialHasMore);
      setHasMore(initialHasMore);
    }
    if (initialCursor !== prevInitialCursor) {
      setPrevInitialCursor(initialCursor);
      setCursor(initialCursor == null ? null : { threadId, id: initialCursor });
    }
  }

  const loadOlder = useCallback(async () => {
    if (!hasMore || isLoadingRef.current) return;

    isLoadingRef.current = true;
    setIsLoadingOlder(true);
    setLoadFailed(false);

    try {
      // olderMessages.length já é quantas mensagens antigas foram
      // acumuladas até aqui — soma direto ao offset, sem contador à parte.
      const offset = olderMessages.length + currentCount;
      const before = cursor?.threadId === threadId ? cursor.id : undefined;
      let page: PagedHistoryResponse;
      try {
        page = await getHistoryPage(threadId, PAGE_SIZE, offset, before);
      } catch (error) {
        if (!(error instanceof HistoryCursorGoneError)) throw error;
        // A mensagem do cursor foi removida: refaz a página por posição.
        page = await getHistoryPage(threadId, PAGE_SIZE, offset);
      }
      setCursor(
        page.next_cursor == null ? null : { threadId, id: page.next_cursor },
      );
      setHasMore(page.has_more);
      setOlderMessages((prev) => [...page.messages, ...prev]);
    } catch {
      // O botão "Carregar anteriores" reaparece para tentar de novo.
      setLoadFailed(true);
    } finally {
      isLoadingRef.current = false;
      setIsLoadingOlder(false);
    }
  }, [threadId, hasMore, currentCount, olderMessages.length, cursor]);

  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !hasMore || loadFailed) return;

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
  }, [sentinelRef, hasMore, loadFailed, loadOlder]);

  return { hasMore, isLoadingOlder, olderMessages, loadFailed, loadOlder };
}
