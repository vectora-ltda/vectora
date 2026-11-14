import { create } from "zustand";
import type { ImageAttachment } from "@/lib/types";

interface OpenedFilesState {
  /** Anexos de arquivos abertos pelo sistema, à espera da próxima conversa nova. */
  pending: ImageAttachment[];
  add: (attachments: ImageAttachment[]) => void;
  /** Entrega os anexos pendentes e os descarta. */
  take: () => ImageAttachment[];
}

/**
 * Ponte entre "Abrir com Vectora" (fora das rotas) e a conversa nova, que só
 * gera o próprio id ao montar: o `ChatInterface` consome os anexos aqui.
 */
export const useOpenedFilesStore = create<OpenedFilesState>()((set, get) => ({
  pending: [],
  add: (attachments) =>
    set((state) => ({ pending: [...state.pending, ...attachments] })),
  take: () => {
    const taken = get().pending;
    set({ pending: [] });
    return taken;
  },
}));
