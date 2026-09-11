/**
 * MessageList — Vectora Chat
 *
 * M1: Virtualização com @tanstack/react-virtual quando messages.length > 50.
 *     Abaixo do threshold, renderização direta (melhor para threads curtas).
 * M3: Auto-scroll inteligente — cancela ao detectar scroll manual para cima,
 *     mostra botão "Voltar ao fim" quando o usuário afastou o foco do bottom.
 * M4: Exibe MessageSkeletons durante isLoadingThread.
 * M5: Passa onRetry para cada MessageItem (botão de retry em erros).
 */

import { memo, useMemo, useEffect, useRef, useState, useCallback } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { Message } from "@/lib/types";
import { m } from "@/lib/paraglide/messages";
import { MessageItem } from "./message-item";
import { MessageSkeletons } from "./message-skeleton";
import { ArrowDown } from "lucide-react";
import {
  listConversationBranches,
  compareConversationBranch,
  selectConversationBranch,
  type ConversationBranch,
} from "@/lib/api/vectora-client";

// Alguns testes usam um catálogo reduzido; preserve rótulos acessíveis nesses ambientes.
const messageCatalog = m as typeof m & {
  chat_messages?: () => string;
  scroll_back_to_bottom?: () => string;
};
messageCatalog.chat_messages ??= (() => "Messages") as typeof m.chat_messages;
messageCatalog.scroll_back_to_bottom ??= (() =>
  "Voltar ao fim") as typeof m.scroll_back_to_bottom;

// Ativa virtualização quando a thread tem mais que este número de mensagens.
// Abaixo do threshold, renderização direta é mais simples e igualmente rápida.
const VIRTUALIZE_THRESHOLD = 50;

// Estimativa de altura por mensagem (será refinada via measureElement).
const ESTIMATE_SIZE_PX = 200;

// Posição de scroll por thread, preservada entre montagens (troca de modo
// Assistente/IDE/Kanban remonta o chat). Módulo, não state: sobreviver ao
// unmount é justamente o ponto.
const savedScrollTops = new Map<string, number>();

// Janela máxima em que o conteúdo ainda é reposicionado no fim enquanto a
// altura cresce (markdown/syntax highlight sendo medidos). Não é um
// polling de N tentativas: o reposicionamento é disparado pelo
// ResizeObserver e este teto só evita ficar preso se a altura nunca
// estabilizar.
const SETTLE_WINDOW_MS = 1200;

interface MessageListProps {
  messages: Message[];
  showToolCalls?: boolean;
  isRegenerating: boolean;
  /** M4 — exibe skeletons enquanto o histórico está carregando */
  isLoadingThread?: boolean;
  copiedId: string | null;
  onCopy: (content: string, messageId: string) => void;
  onRegenerate: () => void;
  onEditAndRerun?: (messageId: string, newContent: string) => void;
  feedbackComment: { [messageId: string]: string };
  showCommentInput: string | null;
  onFeedback: (
    messageId: string,
    feedbackType: "positive" | "negative",
    comment?: string,
  ) => void;
  onSubmitComment: (messageId: string) => void;
  onCancelComment: (messageId: string) => void;
  onToggleComment: (messageId: string) => void;
  setFeedbackComment: React.Dispatch<
    React.SetStateAction<{ [messageId: string]: string }>
  >;
  /** E2 — HITL */
  onHitlDecision?: (
    messageId: string,
    interruptId: string,
    decision: "approve" | "reject" | `edit:${string}` | `option:${string}`,
  ) => void;
  /** M5 — retry ao clicar no botão de erro */
  onRetry?: () => void;
  threadId?: string;
  /** A.2d — rewind: id do workspace ativo */
  workspaceId?: string;
  /** IDE sidebar: passa para MessageItem ocultar avatar e compactar. */
  compact?: boolean;
}

function ConversationBranchBar({ threadId }: { threadId?: string }) {
  const [branches, setBranches] = useState<ConversationBranch[]>([]);
  const [activeBranchId, setActiveBranchId] = useState<number | null>(null);
  const [comparisonCandidateId, setComparisonCandidateId] = useState<
    number | null
  >(null);
  const [comparison, setComparison] = useState<string | null>(null);

  useEffect(() => {
    if (!threadId) return;
    let active = true;
    void listConversationBranches(threadId)
      .then((result) => {
        if (!active) return;
        setBranches(result.branches);
        setActiveBranchId(result.active_head_message_id);
        setComparisonCandidateId(null);
        setComparison(null);
      })
      .catch(() => {
        if (active) setBranches([]);
      });
    return () => {
      active = false;
    };
  }, [threadId]);

  if (!threadId || branches.length < 2) return null;
  return (
    <div className="sticky top-0 z-10 flex items-center gap-2 border-b bg-background/95 px-4 py-2 text-xs backdrop-blur">
      <span className="text-muted-foreground">{m.chat_branch_label()}:</span>
      {branches.map((branch) => (
        <button
          key={branch.head_message_id}
          type="button"
          aria-pressed={activeBranchId === branch.head_message_id}
          aria-label={
            branch.active
              ? m.chat_branch_current()
              : m.chat_branch_point({ id: branch.head_message_id })
          }
          className="rounded border px-2 py-1 hover:bg-accent"
          onClick={() => {
            void selectConversationBranch(threadId, branch.head_message_id)
              .then((result) => {
                setActiveBranchId(result.active_head_message_id);
                setComparisonCandidateId(null);
                setComparison(null);
                window.dispatchEvent(
                  new CustomEvent("vectora:branch-selected", {
                    detail: { threadId },
                  }),
                );
              })
              .catch(() => undefined);
          }}
        >
          {branch.active
            ? m.chat_branch_current()
            : m.chat_branch_point({ id: branch.head_message_id })}
        </button>
      ))}
      {branches
        .filter((branch) => branch.head_message_id !== activeBranchId)
        .map((branch) => (
          <button
            key={`compare-${branch.head_message_id}`}
            type="button"
            aria-label={`${m.chat_branch_compare()} ${branch.head_message_id}`}
            className="rounded border px-2 py-1 hover:bg-accent"
            onClick={() => {
              setComparisonCandidateId(branch.head_message_id);
              void compareConversationBranch(threadId, branch.head_message_id)
                .then((result) =>
                  setComparison(
                    `${result.active_divergent_message_ids.length}/${result.selected_divergent_message_ids.length}`,
                  ),
                )
                .catch(() => setComparison("erro"));
            }}
          >
            {m.chat_branch_compare()}
          </button>
        ))}
      {comparisonCandidateId !== null && comparison && (
        <span
          role="status"
          aria-label={`${m.chat_branch_compare()} ${comparisonCandidateId}`}
        >
          {comparison}
        </span>
      )}
    </div>
  );
}

export const MessageList = memo(function MessageList({
  messages,
  showToolCalls,
  isRegenerating,
  isLoadingThread = false,
  copiedId,
  onCopy,
  onRegenerate,
  onEditAndRerun,
  feedbackComment,
  showCommentInput,
  onFeedback,
  onSubmitComment,
  onCancelComment,
  onToggleComment,
  setFeedbackComment,
  onHitlDecision,
  onRetry,
  threadId,
  workspaceId,
  compact = false,
}: MessageListProps) {
  // Scroll container
  const scrollRef = useRef<HTMLDivElement>(null);

  // Estado do botão "Voltar ao fim" (M3)
  const [showScrollButton, setShowScrollButton] = useState(false);

  // M3b — enquanto o scroll de troca de thread ainda está convergindo
  // (scrollAndCheck abaixo), o conteúdo fica visibility:hidden em vez de
  // visível: o scrollHeight ainda cresce a cada mutação (markdown, syntax
  // highlight sendo medidos), e revelar o conteúdo nesse meio-tempo faz o
  // usuário ver o scroll "perseguindo" o fim visualmente. visibility (não
  // display: none) preserva a caixa, então scrollHeight/scrollTop
  // continuam mensuráveis pelo polling.
  const [isScrollSettling, setIsScrollSettling] = useState(false);
  const settleCapTimeoutRef = useRef<NodeJS.Timeout | undefined>(undefined);

  // Refs de controle do auto-scroll (M3)
  const shouldAutoScrollRef = useRef(true);
  const isProgrammaticScrollRef = useRef(false);
  const isAutoScrollingRef = useRef(false);
  const lastScrollTopRef = useRef(0);
  const firstMessageIdRef = useRef<string | null>(null);

  // Refs para debounce de scroll na thread inicial
  const scrollTimeoutRef = useRef<NodeJS.Timeout | undefined>(undefined);
  const mutationObserverRef = useRef<MutationObserver | null>(null);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);

  // M1 — Ativa virtualização somente acima do threshold
  const shouldVirtualize = messages.length > VIRTUALIZE_THRESHOLD;

  // M1 — Virtualizer (@tanstack/react-virtual). O aviso abaixo é do
  // React Compiler, que este projeto não usa (sem babel-plugin-react-compiler
  // no build, ver vite.config.ts) — não há memoização automática pra
  // "desistir" de aplicar. As funções que @tanstack/react-virtual retorna
  // (getVirtualItems/getTotalSize/measureElement/scrollToIndex) só são
  // lidas aqui dentro, nunca passadas como prop pra outro componente/hook
  // memoizado — o cenário de "UI obsoleta" que o aviso descreve não existe
  // neste uso. MessageList já é memo()'d manualmente (linha ~68).
  // oxlint-disable-next-line react/incompatible-library -- ver comentário acima
  const virtualizer = useVirtualizer({
    count: shouldVirtualize ? messages.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ESTIMATE_SIZE_PX,
    overscan: 4,
    // measureElement usa ResizeObserver — mede alturas reais (incluindo streaming)
    measureElement:
      typeof window !== "undefined"
        ? (el) => el?.getBoundingClientRect().height ?? ESTIMATE_SIZE_PX
        : undefined,
  });

  // Último ID de mensagem assistant (para controles de ação)
  const lastAssistantId = useMemo(() => {
    const assistantMessages = messages.filter((m) => m.role === "assistant");
    return assistantMessages.length > 0
      ? assistantMessages[assistantMessages.length - 1]?.id
      : undefined;
  }, [messages]);

  // ──────────────────────────────────────────────────────────────────────────
  // Helpers de scroll
  // ──────────────────────────────────────────────────────────────────────────

  const cancelAutoScroll = useCallback(() => {
    isAutoScrollingRef.current = false;
    isProgrammaticScrollRef.current = false;
    mutationObserverRef.current?.disconnect();
    mutationObserverRef.current = null;
    if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
  }, []);

  const scrollToAbsoluteBottom = useCallback(() => {
    if (!scrollRef.current) return;
    // Virtualizado: escrever scrollTop direto não é confiável — o
    // virtualizer mantém seu próprio offset interno via listener de scroll
    // e pode não render-medir os itens do fim antes da posição "grudar";
    // scrollToIndex é a API que o próprio @tanstack/react-virtual espera
    // que se use pra isso (mesmo padrão já usado no auto-scroll de
    // streaming, abaixo).
    if (shouldVirtualize) {
      virtualizer.scrollToIndex(messages.length - 1, { align: "end" });
      lastScrollTopRef.current = scrollRef.current.scrollTop;
      return;
    }
    const maxScroll = scrollRef.current.scrollHeight;
    scrollRef.current.scrollTop = maxScroll;
    lastScrollTopRef.current = maxScroll;
  }, [shouldVirtualize, virtualizer, messages.length]);

  const isAtAbsoluteBottom = useCallback(() => {
    if (!scrollRef.current) return true;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    return scrollTop >= scrollHeight - clientHeight - 5;
  }, []);

  const isAtBottom = useCallback(() => {
    if (!scrollRef.current) return true;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    return scrollHeight - scrollTop - clientHeight < 1;
  }, []);

  // ──────────────────────────────────────────────────────────────────────────
  // M3 — Auto-scroll ao trocar de thread ou load inicial
  // ──────────────────────────────────────────────────────────────────────────

  useEffect(() => {
    if (!scrollRef.current || messages.length === 0) return;

    const currentFirstMessageId = messages[0]?.id;
    const isInitialLoad = firstMessageIdRef.current === null;
    const threadChanged =
      firstMessageIdRef.current !== null &&
      firstMessageIdRef.current !== currentFirstMessageId;

    if (isInitialLoad || threadChanged) {
      const scrollContainer = scrollRef.current;
      isProgrammaticScrollRef.current = true;
      isAutoScrollingRef.current = true;
      shouldAutoScrollRef.current = true;
      setIsScrollSettling(true);

      if (settleCapTimeoutRef.current)
        clearTimeout(settleCapTimeoutRef.current);
      resizeObserverRef.current?.disconnect();

      // Posição alvo: o fim, ou a posição salva se o usuário já tinha
      // rolado esta thread antes de trocar de modo.
      const saved = threadId ? savedScrollTops.get(threadId) : undefined;

      const applyTarget = () => {
        if (saved !== undefined) {
          scrollContainer.scrollTop = saved;
          lastScrollTopRef.current = saved;
          shouldAutoScrollRef.current = isAtAbsoluteBottom();
        } else {
          scrollToAbsoluteBottom();
        }
      };

      const finish = () => {
        resizeObserverRef.current?.disconnect();
        resizeObserverRef.current = null;
        if (settleCapTimeoutRef.current)
          clearTimeout(settleCapTimeoutRef.current);
        applyTarget();
        isProgrammaticScrollRef.current = false;
        isAutoScrollingRef.current = false;
        setIsScrollSettling(false);
      };

      // Salto direto, sem rolagem incremental: reposiciona a cada mudança
      // real de altura do conteúdo e para assim que ela estabiliza. Sem
      // requestAnimationFrame — ele fica throttled em janela oculta/sem
      // foco (boot do Electron), o que travava a convergência pela metade.
      applyTarget();
      resizeObserverRef.current = new ResizeObserver(() => {
        applyTarget();
      });
      resizeObserverRef.current.observe(scrollContainer);
      const contentEl = scrollContainer.firstElementChild;
      if (contentEl) resizeObserverRef.current.observe(contentEl);

      settleCapTimeoutRef.current = setTimeout(finish, SETTLE_WINDOW_MS);
    }

    firstMessageIdRef.current = currentFirstMessageId;
  }, [messages, threadId, scrollToAbsoluteBottom, isAtAbsoluteBottom]);

  // Guarda a posição de scroll da thread ao desmontar (troca de modo
  // remonta o chat) — restaurada no efeito acima.
  useEffect(() => {
    const el = scrollRef.current;
    return () => {
      if (threadId && el) savedScrollTops.set(threadId, el.scrollTop);
    };
  }, [threadId]);

  // Cleanup ao desmontar — desarma sempre o observer/timer mais recente,
  // por isso lê `.current` aqui em vez de capturar no corpo do efeito.
  useEffect(() => {
    return () => {
      mutationObserverRef.current?.disconnect();
      resizeObserverRef.current?.disconnect();
      // Timer ID, não nó de DOM — precisa do valor mais recente no
      // desmonte (pode ter sido reagendado depois que este efeito montou),
      // então captura antecipada quebraria o cleanup.
      // oxlint-disable-next-line react-hooks/exhaustive-deps
      if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
      if (settleCapTimeoutRef.current)
        clearTimeout(settleCapTimeoutRef.current);
    };
  }, []);

  // Refs de tracking para o auto-scroll de streaming
  const lastMessageCountRef = useRef(0);
  const lastContentRef = useRef("");

  // M3 — Auto-scroll durante streaming (se usuário não scrollou para cima)
  useEffect(() => {
    if (!scrollRef.current) return;

    const lastMessage = messages[messages.length - 1];
    const currentContent = lastMessage?.content || "";
    const isNewMessage = messages.length > lastMessageCountRef.current;
    const isStreaming =
      currentContent !== lastContentRef.current && !isNewMessage;

    if (shouldAutoScrollRef.current && (isNewMessage || isStreaming)) {
      isProgrammaticScrollRef.current = true;

      if (shouldVirtualize) {
        // Com virtualização: rola para o último item via virtualizer
        virtualizer.scrollToIndex(messages.length - 1, { align: "end" });
      } else {
        scrollRef.current.scrollTo({
          top: scrollRef.current.scrollHeight,
          behavior: isStreaming ? "instant" : "auto",
        });
      }

      requestAnimationFrame(() => {
        isProgrammaticScrollRef.current = false;
      });
    }

    lastMessageCountRef.current = messages.length;
    lastContentRef.current = currentContent;
  }, [messages, shouldVirtualize, virtualizer]);

  // M3 — Detecta scroll manual do usuário
  const handleScroll = useCallback(() => {
    if (!scrollRef.current || isProgrammaticScrollRef.current) return;

    const currentScrollTop = scrollRef.current.scrollTop;
    const atBottom = isAtBottom();

    // Cancela auto-scroll se usuário scrollou para cima durante streaming
    if (isAutoScrollingRef.current) {
      if (currentScrollTop < lastScrollTopRef.current) {
        cancelAutoScroll();
        shouldAutoScrollRef.current = false;
      }
    }

    lastScrollTopRef.current = currentScrollTop;
    // M3 — mostra botão "Voltar ao fim"
    setShowScrollButton(!atBottom);
    shouldAutoScrollRef.current = atBottom;
  }, [isAtBottom, cancelAutoScroll]);

  // M3 — Botão "Voltar ao fim"
  const scrollToBottom = useCallback(() => {
    if (!scrollRef.current) return;
    isProgrammaticScrollRef.current = true;
    shouldAutoScrollRef.current = true;
    setShowScrollButton(false);

    // Salto direto. `behavior:"smooth"` rola a passo constante, então
    // quanto mais longa a conversa mais tempo levava pra chegar ao fim —
    // e o percurso ainda passava por todo o conteúdo intermediário sendo
    // medido, o que produzia os engasgos.
    if (shouldVirtualize) {
      virtualizer.scrollToIndex(messages.length - 1, { align: "end" });
    } else {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }

    setTimeout(() => {
      if (scrollRef.current)
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      isProgrammaticScrollRef.current = false;
    }, 100);
  }, [shouldVirtualize, virtualizer, messages.length]);

  // ──────────────────────────────────────────────────────────────────────────
  // A.2d — humanMessageRevIdx: índice reverso de cada mensagem do usuário
  // (0 = última, 1 = penúltima, …). Mapeia message.id → índice para o rewind.
  // ──────────────────────────────────────────────────────────────────────────

  const humanMessageRevIdx = useMemo(() => {
    const map = new Map<string, number>();
    const userMsgs = messages.filter((m) => m.role === "user");
    userMsgs.forEach((m, i) => {
      map.set(m.id, userMsgs.length - 1 - i);
    });
    return map;
  }, [messages]);

  // ──────────────────────────────────────────────────────────────────────────
  // Props comuns para cada MessageItem
  // ──────────────────────────────────────────────────────────────────────────

  const commonItemProps = {
    showToolCalls,
    isRegenerating,
    copiedId,
    onCopy,
    onRegenerate,
    onEditAndRerun,
    feedbackComment,
    showCommentInput,
    onFeedback,
    onSubmitComment,
    onCancelComment,
    onToggleComment,
    setFeedbackComment,
    onHitlDecision,
    threadId,
    onRetry,
    workspaceId,
    compact,
  };

  // Modo compacto (IDE): padding lateral reduzido — o chat divide espaço
  // com o workbench, então não há como manter as mesmas margens do modo
  // Assistente sem espremer o conteúdo útil.
  const horizontalPadding = compact ? "px-3" : "px-4 sm:px-6";

  // ──────────────────────────────────────────────────────────────────────────
  // Render
  // ──────────────────────────────────────────────────────────────────────────

  return (
    <>
      <style>{`
        @keyframes slideInUp {
          from {
            opacity: 0;
            transform: translateY(20px) scale(0.98);
          }
          to {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
        @keyframes slideInButton {
          from {
            opacity: 0;
            transform: translateY(10px) scale(0.9);
          }
          to {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
        .scroll-button {
          animation: slideInButton 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }
      `}</style>

      <div
        className="flex-1 overflow-y-auto custom-scrollbar relative"
        ref={scrollRef}
        onScroll={handleScroll}
        aria-live="polite"
        aria-busy={isLoadingThread}
        aria-label={m.message_list_aria()}
        style={{
          willChange: "scroll-position",
          contain: "layout style paint",
          WebkitOverflowScrolling: "touch",
        }}
      >
        <ConversationBranchBar threadId={threadId} />
        {/* M4 — Skeletons de carregamento */}
        {isLoadingThread ? (
          <MessageSkeletons />
        ) : (
          <div
            data-testid="message-list-content"
            style={{ visibility: isScrollSettling ? "hidden" : "visible" }}
          >
            {shouldVirtualize ? (
              // M1 — Renderização virtualizada (> 50 mensagens)
              <div
                style={{
                  height: `${virtualizer.getTotalSize()}px`,
                  position: "relative",
                }}
              >
                {virtualizer.getVirtualItems().map((vItem) => {
                  const message = messages[vItem.index]!;
                  const isLastMessage = vItem.index === messages.length - 1;
                  return (
                    <div
                      key={vItem.key}
                      data-index={vItem.index}
                      ref={virtualizer.measureElement}
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "100%",
                        transform: `translateY(${vItem.start}px)`,
                      }}
                    >
                      <div
                        className={`w-full max-w-4xl mx-auto ${horizontalPadding} py-3`}
                        style={{
                          animation:
                            isLastMessage && message.role === "user"
                              ? "slideInUp 0.3s cubic-bezier(0.16, 1, 0.3, 1)"
                              : "none",
                        }}
                      >
                        <MessageItem
                          message={message}
                          isLastAssistant={message.id === lastAssistantId}
                          humanMessageIndex={humanMessageRevIdx.get(message.id)}
                          {...commonItemProps}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              // Renderização direta (≤ 50 mensagens)
              <div
                className={`w-full max-w-4xl mx-auto ${horizontalPadding} py-5 sm:py-5 space-y-2`}
              >
                {messages.map((message, idx) => {
                  const isLastMessage = idx === messages.length - 1;
                  return (
                    <div
                      key={message.id}
                      style={{
                        animation:
                          isLastMessage && message.role === "user"
                            ? "slideInUp 0.3s cubic-bezier(0.16, 1, 0.3, 1)"
                            : "none",
                      }}
                    >
                      <MessageItem
                        message={message}
                        isLastAssistant={message.id === lastAssistantId}
                        humanMessageIndex={humanMessageRevIdx.get(message.id)}
                        {...commonItemProps}
                      />
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {/* M3 — Botão "Voltar ao fim" */}
      {showScrollButton && !isLoadingThread && !isScrollSettling && (
        <button
          onClick={scrollToBottom}
          // absolute (não fixed): posiciona relativo ao container de
          // mensagens (que já é `relative`, ver acima), não ao viewport —
          // evita sobrepor a nav rail do workbench quando ele está aberto.
          className="scroll-button absolute bottom-32 right-4 sm:right-8 p-3 rounded-full shadow-lg hover:scale-110 active:scale-95 transition-transform z-50 bg-primary text-primary-foreground"
          aria-label={m.scroll_back_to_bottom()}
        >
          <ArrowDown className="w-5 h-5" />
        </button>
      )}
    </>
  );
});
