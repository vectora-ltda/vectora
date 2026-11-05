import { create } from "zustand";

/**
 * Mapa de threads com stream SSE ativo (não um único valor global) — mais de
 * uma thread pode estar streamando ao mesmo tempo (ex.: usuário manda
 * mensagem numa thread, navega antes de terminar, manda outra em thread
 * diferente). Um valor único (`streamingThreadId: string | null`) perdia o
 * indicador da 1ª thread assim que a 2ª começava — regressão real: sessão
 * ainda em stream sumia da sidebar como "parada" sem nunca ter terminado.
 */
interface StreamingStore {
  streaming: Record<string, boolean>;
  setStreaming: (threadId: string, value: boolean) => void;
  isStreaming: (threadId: string) => boolean;
}

export const useStreamingStore = create<StreamingStore>((set, get) => ({
  streaming: {},

  setStreaming: (threadId, value) => {
    if (!threadId) return;
    set((state) => {
      const isSet = Boolean(state.streaming[threadId]);
      if (isSet === value) return state;
      const next = { ...state.streaming };
      if (value) next[threadId] = true;
      else delete next[threadId];
      return { streaming: next };
    });
  },

  isStreaming: (threadId) => Boolean(get().streaming[threadId]),
}));
