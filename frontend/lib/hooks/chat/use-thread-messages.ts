/**
 * useThreadMessages — bridge entre o store Zustand e a API do React useState.
 *
 * Drop-in replacement para ``useState<Message[]>``, com a diferença de que
 * as mensagens ficam cacheadas por threadId no ``useThreadsStore``. Trocar de
 * thread e voltar exibe o conteúdo instantaneamente (sem flash vazio).
 *
 * @example
 *   const [messages, setMessages] = useThreadMessages(threadId)
 *   // ...uso idêntico a useState
 */

import { useCallback } from "react";
import type { Message } from "@/lib/types";
import { useThreadsStore } from "@/lib/stores/threads-store";

type Updater = Message[] | ((prev: Message[]) => Message[]);
type SetMessages = (updater: Updater) => void;

export function useThreadMessages(threadId: string): [Message[], SetMessages] {
  // Leitura: subscreve apenas ao slice desta thread. Quando o store muda
  // mensagens de outras threads, este componente NÃO re-renderiza.
  const messages = useThreadsStore(
    (s) => s.cache[threadId]?.messages ?? EMPTY_ARRAY,
  );

  const patchMessages = useThreadsStore((s) => s.patchMessages);
  const setMessagesAction = useThreadsStore((s) => s.setMessages);

  const setMessages: SetMessages = useCallback(
    (updater) => {
      if (typeof updater === "function") {
        patchMessages(threadId, updater);
      } else {
        setMessagesAction(threadId, updater);
      }
    },
    [threadId, patchMessages, setMessagesAction],
  );

  return [messages, setMessages];
}

// Constante para evitar criar novo array vazio a cada render — o seletor
// usa identidade referencial e isso previne re-render desnecessário.
const EMPTY_ARRAY: Message[] = [];
