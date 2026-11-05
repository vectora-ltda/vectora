import { useEffect } from "react";

import { useWorkbenchStore } from "@/lib/stores/workbench-store";

/**
 * Carrega os arquivos fixados da sessão a partir do backend (fonte de verdade,
 * §8) ao montar ou trocar de thread. O cache local do workbench-store é
 * reconciliado com a resposta — os pins não persistem mais em localStorage.
 */
export function usePins(threadId: string): void {
  const loadPins = useWorkbenchStore((s) => s.loadPins);
  useEffect(() => {
    if (threadId) void loadPins(threadId);
  }, [threadId, loadPins]);
}
