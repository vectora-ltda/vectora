/**
 * chat-input-store — estado do campo de mensagem do chat.
 *
 * Duas responsabilidades distintas:
 *
 *  1. **Injeção cross-component (volátil):** outras áreas pré-populam o input
 *     sem acoplamento direto. `pushDraft(texto)` substitui o input inteiro
 *     (ex.: template do PlanTab); `pushMention(path)` adiciona `@path`. O
 *     ChatInterface observa e consome (limpa) após aplicar.
 *
 *  2. **Rascunhos por thread (persistido):** o texto digitado e não enviado de
 *     cada conversa, preservado ao trocar de thread e ao recarregar a página.
 *     Só `drafts` é persistido (`vectora-chat-drafts`); a injeção é efêmera.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface ChatInputState {
  // ── Injeção cross-component (volátil) ──────────────────────────────────────
  draft: string | null;
  /** Path adicionado via painel de arquivos → inserido como @path no input. */
  mention: string | null;
  pushDraft: (text: string) => void;
  consumeDraft: () => void;
  pushMention: (path: string) => void;
  consumeMention: () => void;

  // ── Rascunhos por thread (persistido) ──────────────────────────────────────
  drafts: Record<string, string>;
  getDraft: (threadId: string) => string;
  setDraft: (threadId: string, text: string) => void;
  clearDraft: (threadId: string) => void;
}

export const useChatInputStore = create<ChatInputState>()(
  persist(
    (set, get) => ({
      draft: null,
      mention: null,
      pushDraft: (text) => set({ draft: text }),
      consumeDraft: () => set({ draft: null }),
      pushMention: (path) => set({ mention: path }),
      consumeMention: () => set({ mention: null }),

      drafts: {},
      getDraft: (threadId) => get().drafts[threadId] ?? "",
      setDraft: (threadId, text) =>
        set((s) => {
          // Texto vazio remove a entrada (não acumula rascunhos vazios).
          if (!text) {
            if (!(threadId in s.drafts)) return s;
            const next = { ...s.drafts };
            delete next[threadId];
            return { drafts: next };
          }
          if (s.drafts[threadId] === text) return s;
          return { drafts: { ...s.drafts, [threadId]: text } };
        }),
      clearDraft: (threadId) =>
        set((s) => {
          if (!(threadId in s.drafts)) return s;
          const next = { ...s.drafts };
          delete next[threadId];
          return { drafts: next };
        }),
    }),
    {
      name: "vectora-chat-drafts",
      // A injeção (draft/mention) é efêmera — só os rascunhos por thread persistem.
      partialize: (s) => ({ drafts: s.drafts }),
    },
  ),
);
