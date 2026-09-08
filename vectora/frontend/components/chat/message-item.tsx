import {
  Copy,
  Check,
  Settings,
  RefreshCw,
  ThumbsUp,
  ThumbsDown,
  MessageSquare,
  RotateCcw,
  Download,
  X,
  AlertTriangle,
  Volume2,
  Pause,
  Play,
  Square,
} from "lucide-react";
import { ToolCallRenderer } from "./tool-call-renderer";
import { AgentStatusLine } from "./agent-status-line";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";
import { ThinkingTimer } from "./animations/thinking-timer";
import { AnimatedThinking } from "./animations/animated-thinking";
import { HITLPanel } from "./features/hitl-panel";
import type { RagCitation } from "./features/rag-citation-popover";
import type { Message } from "@/lib/types";
import { stripMarkdownEnvelope } from "@/lib/utils/string";
import { estimateCost, formatCost } from "@/lib/config/model-prices";
import { useState, useMemo, useEffect, useCallback, memo, useRef } from "react";
import { useTheme } from "next-themes";
import { useToastStore } from "@/lib/stores/toast-store";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { formatDistanceToNow } from "date-fns";
import { ptBR, es as esLocale, enUS } from "date-fns/locale";
import { useQueryClient } from "@tanstack/react-query";
import { m } from "@/lib/paraglide/messages";
import { useSpeechSynthesis } from "@/lib/hooks/use-speech-synthesis";

/** Locale do date-fns a partir do idioma da UI (item 9 — "há quanto tempo"). */
const DATE_FNS_LOCALES = { pt: ptBR, es: esLocale, en: enUS } as const;

const WEB_TOOLS = new Set(["web_search", "fetch_url", "web_fetch"]);

/** Chips de fonte (RAG + domínios web) para o painel de atividade (deep research). */
function activitySources(message: Message): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const push = (label: string) => {
    if (label && !seen.has(label)) {
      seen.add(label);
      out.push(label);
    }
  };
  for (const c of message.ragCitations ?? []) {
    push((c.source ?? "").split(/[/\\]/).pop() || c.source);
  }
  for (const call of message.toolCalls ?? []) {
    if (!WEB_TOOLS.has(call.name)) continue;
    const args = (call.args ?? {}) as Record<string, unknown>;
    if (typeof args.url === "string") {
      try {
        push(new URL(args.url).hostname);
      } catch {
        push(args.url);
      }
    } else if (typeof args.query === "string") {
      push(args.query);
    }
  }
  return out.slice(0, 8);
}

// ============================================================================
// Constants
// ============================================================================

const COPY_FEEDBACK_DURATION = 2000;

function getCodeColors(isDark: boolean) {
  if (isDark) {
    return {
      blockBackground: "oklch(0.16 0 0)",
      blockBorder: "oklch(0.30 0 0)",
      inlineBackground: "oklch(0.22 0 0)",
      inlineBorder: "oklch(0.32 0 0)",
      primary: "#7FC8FF",
      primaryLight: "#99D3FF",
      primaryDark: "#B2DEFF",
      blue: "#60a5fa",
      yellow: "#fbbf24",
      orange: "#f59e0b",
      green: "#10b981",
      red: "#ef4444",
      text: "#e4e4e7",
      comment: "#6b7280",
      punctuation: "#a1a1aa",
    };
  }
  return {
    blockBackground: "#f8f9fa",
    blockBorder: "#e1e4e8",
    inlineBackground: "#f0f2f4",
    inlineBorder: "#dde1e7",
    primary: "#0366d6",
    primaryLight: "#032f62",
    primaryDark: "#005cc5",
    blue: "#6f42c1",
    yellow: "#b08800",
    orange: "#e36209",
    green: "#22863a",
    red: "#cb2431",
    text: "#24292e",
    comment: "#6a737d",
    punctuation: "#586069",
  };
}

// ============================================================================
// Syntax Highlighting Theme
// ============================================================================

function getCodeTheme(isDark: boolean) {
  const c = getCodeColors(isDark);
  return {
    ...(isDark ? vscDarkPlus : {}),
    'pre[class*="language-"]': {
      ...(isDark ? vscDarkPlus['pre[class*="language-"]'] : {}),
      background: c.blockBackground,
      border: `1px solid ${c.blockBorder}`,
      borderRadius: "8px",
      padding: "1rem",
      margin: "0.75rem 0",
    },
    'code[class*="language-"]': {
      ...(isDark ? vscDarkPlus['code[class*="language-"]'] : {}),
      background: "transparent",
      color: c.text,
      fontSize: "13px",
      lineHeight: "1.6",
    },
    comment: { color: c.comment },
    prolog: { color: c.comment },
    doctype: { color: c.comment },
    cdata: { color: c.comment },
    punctuation: { color: c.punctuation },
    property: { color: c.primary },
    tag: { color: c.primary },
    operator: { color: c.primary },
    entity: { color: c.primary },
    url: { color: c.primary },
    "attr-name": { color: c.primary },
    string: { color: c.primaryLight },
    char: { color: c.primaryLight },
    "attr-value": { color: c.primaryLight },
    builtin: { color: c.primaryDark },
    atrule: { color: c.primaryDark },
    keyword: { color: c.primaryDark },
    boolean: { color: c.orange },
    number: { color: c.orange },
    constant: { color: c.orange },
    symbol: { color: c.orange },
    regex: { color: c.orange },
    selector: { color: c.green },
    inserted: { color: c.green },
    function: { color: c.blue },
    "class-name": { color: c.yellow },
    variable: { color: c.text },
    important: { color: c.red, fontWeight: "bold" },
    deleted: { color: c.red },
  };
}

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Recursively extract text content from ReactMarkdown nodes
 * Used for extracting code from markdown code blocks for copy functionality
 */
const extractTextFromNode = (node: any): string => {
  if (typeof node === "string") return node;
  if (node?.props?.children) {
    if (typeof node.props.children === "string") {
      return node.props.children;
    }
    if (Array.isArray(node.props.children)) {
      return node.props.children.map(extractTextFromNode).join("");
    }
    return extractTextFromNode(node.props.children);
  }
  return "";
};

/**
 * Individual code block component with its own copy state
 * This prevents the copy button from flickering during streaming
 */
const CodeBlock = memo(
  ({ codeString, language }: { codeString: string; language: string }) => {
    const [isCopied, setIsCopied] = useState(false);
    const { resolvedTheme } = useTheme();
    const isDark = resolvedTheme !== "light";
    const colors = getCodeColors(isDark);
    const codeTheme = getCodeTheme(isDark);

    const handleCopyCode = useCallback(() => {
      navigator.clipboard.writeText(codeString);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), COPY_FEEDBACK_DURATION);
    }, [codeString]);

    return (
      <div className="relative group my-4">
        <SyntaxHighlighter
          language={language}
          style={codeTheme}
          customStyle={{
            margin: "0.75rem 0",
            background: colors.blockBackground,
            border: `1px solid ${colors.blockBorder}`,
            borderRadius: "8px",
            padding: "1rem",
          }}
          codeTagProps={{
            style: {
              fontSize: "13px",
              fontFamily: "var(--font-mono), ui-monospace, monospace",
            },
          }}
        >
          {codeString}
        </SyntaxHighlighter>
        <button
          onClick={handleCopyCode}
          className="absolute top-2 right-2 sm:top-3 sm:right-3 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity duration-200 px-2 sm:px-2.5 py-1 sm:py-1.5 rounded-md text-xs flex items-center gap-1 sm:gap-1.5 backdrop-blur-sm"
          style={{
            background: isDark
              ? "rgba(0, 0, 0, 0.7)"
              : "rgba(255, 255, 255, 0.85)",
            color: colors.text,
            border: `1px solid ${colors.blockBorder}`,
            willChange: "opacity",
          }}
          aria-label="Copy code to clipboard"
        >
          {isCopied ? (
            <>
              <Check className="w-3.5 h-3.5" />
              Copied
            </>
          ) : (
            <>
              <Copy className="w-3.5 h-3.5" />
              Copy
            </>
          )}
        </button>
      </div>
    );
  },
);

// ============================================================================
// C.28 — RagCitationList: lista colapsável de fontes RAG
// ============================================================================

function RagCitationList({ citations }: { citations: RagCitation[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2 text-xs">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors"
        aria-expanded={open}
      >
        <span className="text-[10px] font-mono bg-primary/10 text-primary px-1 rounded">
          {citations.length} fonte{citations.length !== 1 ? "s" : ""}
        </span>
        <span className="text-[10px]">RAG</span>
        <span className="text-[9px] opacity-60">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="mt-1.5 space-y-1.5">
          {citations.map((c) => (
            <div
              key={c.index}
              className="rounded border border-border/40 bg-muted/20 px-2 py-1.5"
            >
              <p className="text-[10px] font-medium text-primary truncate">
                [{c.index}] {c.source || "Fonte desconhecida"}
              </p>
              {c.chunk && (
                <p className="text-[10px] text-muted-foreground mt-0.5 line-clamp-2">
                  {c.chunk}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface MessageItemProps {
  message: Message;
  showToolCalls?: boolean;
  isLastAssistant: boolean;
  isRegenerating: boolean;
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
  /** E2 — HITL: callback disparado quando o usuário aprova/rejeita/edita */
  onHitlDecision?: (
    messageId: string,
    interruptId: string,
    decision: "approve" | "reject" | `edit:${string}`,
  ) => void;
  /** E2 — threadId necessário para contextualizar o painel HITL */
  threadId?: string;
  /** M5 — callback de retry para mensagens com isError=true */
  onRetry?: () => void;
  /** A.2d — rewind: id do workspace ativo para restaurar estado */
  workspaceId?: string;
  /**
   * A.2d — rewind: índice desta mensagem entre as mensagens do usuário,
   * contando do fim (0 = última mensagem do usuário).
   * Mapeia diretamente ao índice na lista de checkpoints (DESC).
   */
  humanMessageIndex?: number;
  /** C.29 — modelo ativo da sessão, para cálculo do badge de custo. */
  modelId?: string;
  /** IDE sidebar: oculta avatar e reduz gap entre mensagens. */
  compact?: boolean;
}

export const MessageItem = memo(
  function MessageItem({
    message,
    showToolCalls,
    isLastAssistant,
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
    threadId = "",
    onRetry,
    workspaceId,
    humanMessageIndex,
    modelId,
    compact = false,
  }: MessageItemProps) {
    const uiLang = useSettingsStore((s) => s.language);
    const { resolvedTheme } = useTheme();
    const isDark = resolvedTheme !== "light";
    const [isEditing, setIsEditing] = useState(false);
    const [editContent, setEditContent] = useState(message.content);
    const [editError] = useState<string | null>(null);
    const prevContentRef = useRef(message.content);
    const speech = useSpeechSynthesis(message.content, threadId);

    // Sync editContent when message.content changes (e.g., during streaming)
    useEffect(() => {
      if (!isEditing && message.content !== prevContentRef.current) {
        setEditContent(message.content);
        prevContentRef.current = message.content;
      }
    }, [message.content, isEditing]);

    const setLimitedEditContent = useCallback((value: string) => {
      setEditContent(value);
    }, []);

    const handleStartEdit = useCallback(() => {
      setEditContent(message.content);
      setIsEditing(true);
    }, [message.content]);

    const handleSaveEdit = useCallback(() => {
      if (editContent.trim() && onEditAndRerun) {
        onEditAndRerun(message.id, editContent.trim());
        setIsEditing(false);
      }
    }, [editContent, onEditAndRerun, message.id]);

    const handleCancelEdit = useCallback(() => {
      setEditContent(message.content);
      setIsEditing(false);
    }, [message.content]);

    // ── A.2d — Rewind ─────────────────────────────────────────────────────
    const [rewindOpen, setRewindOpen] = useState(false);
    const [rewinding, setRewinding] = useState(false);
    const queryClient = useQueryClient();

    // ── Trilha C — Lightbox de imagem ───────────────────────────────────
    const [lightboxImage, setLightboxImage] = useState<{
      src: string;
      name: string;
    } | null>(null);

    const handleRewind = useCallback(async () => {
      if (!threadId || !workspaceId || humanMessageIndex === undefined) return;
      setRewinding(true);
      try {
        const res = await fetch(
          `/threads/${encodeURIComponent(threadId)}/checkpoints`,
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as {
          checkpoints: { checkpoint_id: string }[];
        };
        const checkpoints = data.checkpoints ?? [];
        const target = checkpoints[humanMessageIndex];
        if (!target) {
          useToastStore.getState().error(m.chat_rewind_no_checkpoint());
          setRewindOpen(false);
          return;
        }
        const qs = new URLSearchParams({ workspace_id: workspaceId });
        const rewindRes = await fetch(
          `/threads/${encodeURIComponent(threadId)}/rewind?${qs}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              checkpoint_id: target.checkpoint_id,
              // `id` da própria mensagem no SessionStore (ver
              // HistoryMessage.checkpoint_id) — o backend reaponta a
              // branch ativa da conversa pra este ponto, truncando o
              // histórico junto com os arquivos do workspace.
              message_checkpoint_id: message.checkpointId ?? "",
            }),
          },
        );
        if (rewindRes.status === 409) {
          useToastStore.getState().error(m.chat_rewind_busy());
        } else if (!rewindRes.ok) {
          useToastStore.getState().error(m.chat_rewind_error());
        } else {
          useToastStore.getState().success(m.chat_rewind_ok());
          // Sem isso a UI continuava mostrando as mensagens truncadas até
          // um F5 — o backend já truncou, mas nada local sabia da mudança.
          // invalidateQueries cobre a próxima navegação (cache do router
          // loader); o evento cobre o ChatInterface já montado agora.
          await queryClient.invalidateQueries({
            queryKey: ["thread-history", threadId],
          });
          document.dispatchEvent(
            new CustomEvent("vectora:thread-rewound", { detail: { threadId } }),
          );
        }
      } catch {
        useToastStore.getState().error(m.chat_rewind_error());
      } finally {
        setRewinding(false);
        setRewindOpen(false);
      }
    }, [
      threadId,
      workspaceId,
      humanMessageIndex,
      message.checkpointId,
      queryClient,
    ]);

    // Track code block index to generate stable IDs during streaming
    const codeBlockIndexRef = useRef(0);

    // Reset counter before each render so code blocks get consistent
    // indices. O `code:` renderer em markdownComponents (memoizado, chamado
    // de forma síncrona pelo ReactMarkdown durante ESTE render) incrementa
    // o ref logo abaixo — um useEffect rodaria tarde demais pra zerar antes
    // da própria renderização usá-lo. Não afeta o que é renderizado (é só
    // geração de id estável), só bookkeeping interno do render em curso.
    // oxlint-disable-next-line react/refs
    codeBlockIndexRef.current = 0;

    // Memoize markdown components to prevent button remounting during streaming
    const markdownComponents = useMemo(() => {
      const colors = getCodeColors(isDark);
      return {
        // Custom link renderer - opens in new tab
        a: ({ children, ...props }: any) => (
          <a
            {...props}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              color: colors.primary,
              textDecorationColor: colors.primary,
            }}
          >
            {children}
          </a>
        ),

        // Custom code renderer - handles both inline code and code blocks
        code: ({
          node,
          inline,
          className,
          children,
          jsx: _jsx,
          ...props
        }: any) => {
          const match = /language-(\w+)/.exec(className || "");
          const language = match ? match[1] : "text";
          const codeString = String(children).replace(/\n$/, "");

          // Check if it's inline code: single backticks or no newlines
          const isInlineCode =
            inline === true || (!className && !codeString.includes("\n"));

          // Inline code (single backticks) - Slack-style highlighting
          if (isInlineCode) {
            return (
              <code
                className="px-1.5 py-0.5 text-[13px] font-mono"
                style={{
                  backgroundColor: colors.inlineBackground,
                  color: colors.primary,
                  border: `1px solid ${colors.inlineBorder}`,
                  borderRadius: "5px",
                }}
                {...props}
              >
                {children}
              </code>
            );
          }

          // Code blocks (triple backticks) - use stable ID based on position, not content
          // This prevents flickering during streaming when code content changes
          const blockIndex = codeBlockIndexRef.current++;
          const codeBlockId = `${message.id}-code-${blockIndex}`;

          // Render a separate component for the code block with copy functionality
          return (
            <CodeBlock
              key={codeBlockId}
              codeString={codeString}
              language={language}
            />
          );
        },
      };
    }, [message.id, isDark]);

    return (
      <>
        <style>{`
          @keyframes dance {
            0% {
              transform: rotate(-30deg) scale(1);
            }
            25% {
              transform: rotate(0deg) scale(1.05);
            }
            50% {
              transform: rotate(30deg) scale(1);
            }
            75% {
              transform: rotate(0deg) scale(1.05);
            }
            100% {
              transform: rotate(-30deg) scale(1);
            }
          }
          @keyframes fadeIn {
            from {
              opacity: 0;
              transform: translateY(5px);
            }
            to {
              opacity: 1;
              transform: translateY(0);
            }
          }
          .dancing {
            animation: dance 0.8s ease-in-out infinite;
          }

          /* Smooth text rendering optimizations */
          .prose {
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
            text-rendering: optimizeLegibility;
          }

          /* Optimize layout performance during streaming */
          .prose > * {
            transition: opacity 0.1s ease-out;
          }

          /* Cursor piscante pré-1º token */
          @keyframes blink {
            0%,
            100% {
              opacity: 1;
            }
            50% {
              opacity: 0;
            }
          }
        `}</style>
        <div
          className={`flex items-start group/message ${compact ? "gap-2" : "gap-3 sm:gap-4"} ${message.role === "user" ? "justify-end" : ""}`}
        >
          <div
            className={`min-w-0 space-y-2 ${message.role === "user" ? (compact ? "max-w-[90%]" : "max-w-[85%]") : "flex-1"}`}
          >
            <div
              className={`rounded-lg px-3 py-2 transition-all duration-150 ease-out ${message.role === "user" ? "bg-muted text-foreground" : "text-foreground"}`}
              style={{
                willChange: message.isThinking ? "contents" : "auto",
                contain: "layout style paint",
              }}
            >
              {/* D2 — Progress semântico durante streaming */}
              {message.role === "assistant" &&
                (message.isThinking ||
                  (message.thinkingSteps &&
                    message.thinkingSteps.length > 0)) && (
                  <details open className="mb-3 text-xs">
                    <summary className="cursor-pointer flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors">
                      {message.isThinking && (
                        <div className="w-2 h-2 rounded-full bg-primary animate-pulse" />
                      )}
                      <span>
                        {message.isThinking ? (
                          message.currentNodeLabel ? (
                            <span className="text-muted-foreground">
                              {message.currentNodeLabel}
                            </span>
                          ) : (
                            <AnimatedThinking />
                          )
                        ) : (
                          <span className="font-medium">
                            {m.chat_agent_steps()}
                          </span>
                        )}{" "}
                        ({message.thinkingSteps?.length || 0})
                      </span>
                      {message.isThinking && (
                        <>
                          <span className="ml-1">•</span>
                          <ThinkingTimer
                            startTime={message.thinkingStartTime}
                            duration={message.thinkingDuration}
                            isThinking={!!message.isThinking}
                          />
                        </>
                      )}
                    </summary>
                    {message.thinkingSteps &&
                      message.thinkingSteps.length > 0 && (
                        <div className="mt-2 ml-1 space-y-1.5 border-l border-border/60 pl-3 text-muted-foreground text-[11px]">
                          {message.thinkingSteps.map((step, idx) => (
                            <div
                              key={`${message.id}-step-${idx}`}
                              className="relative flex items-start gap-2"
                            >
                              <span className="absolute -left-[15px] top-1.5 h-1.5 w-1.5 rounded-full bg-primary/50" />
                              <span>{step}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    {(() => {
                      const src = activitySources(message);
                      return src.length > 0 ? (
                        <div className="mt-2 flex flex-wrap gap-1 pl-1">
                          {src.map((s, i) => (
                            <span
                              key={`${message.id}-src-${i}`}
                              className="inline-flex max-w-[180px] items-center truncate rounded-full border border-border/60 bg-muted/40 px-2 py-0.5 text-[10px] text-muted-foreground"
                            >
                              {s}
                            </span>
                          ))}
                        </div>
                      ) : null;
                    })()}
                  </details>
                )}

              {message.role === "user" ? (
                isEditing ? (
                  <div>
                    <Textarea
                      value={editContent}
                      onChange={(e) => setLimitedEditContent(e.target.value)}
                      className="min-h-[80px] text-sm"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          handleSaveEdit();
                        } else if (e.key === "Escape") {
                          handleCancelEdit();
                        }
                      }}
                      onBlur={handleCancelEdit}
                      onFocus={(e) => {
                        // Select all text on focus for easier editing
                        e.target.select();
                      }}
                    />
                    {editError && (
                      <div className="mt-1 text-xs text-destructive">
                        {editError}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="space-y-2">
                    {/* File attachments - uniform grid layout */}
                    {message.images && message.images.length > 0 && (
                      <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-2 mb-3">
                        {message.images.map((file) => {
                          const isImage = file.mimeType?.startsWith("image/");
                          const fileName = file.name || "File";
                          const fileExt = fileName
                            .split(".")
                            .pop()
                            ?.toLowerCase();
                          const fileSizeKB = file.size
                            ? Math.round(file.size / 1024)
                            : 0;

                          return (
                            <div
                              key={file.id}
                              className="h-32 rounded-lg border-2 border-border bg-muted/30 hover:bg-muted/50 hover:border-primary transition-all flex flex-col overflow-hidden"
                            >
                              {isImage ? (
                                // Image with filename overlay
                                <button
                                  type="button"
                                  className="relative h-full w-full cursor-zoom-in"
                                  onClick={() =>
                                    setLightboxImage({
                                      src:
                                        file.url ||
                                        `data:${file.mimeType};base64,${file.base64}`,
                                      name: fileName,
                                    })
                                  }
                                >
                                  <img
                                    src={
                                      file.url ||
                                      `data:${file.mimeType};base64,${file.base64}`
                                    }
                                    alt={fileName}
                                    className="h-full w-full object-cover"
                                  />
                                  <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent px-2 py-1">
                                    <p
                                      className="text-xs text-white truncate"
                                      title={fileName}
                                    >
                                      {fileName}
                                    </p>
                                  </div>
                                </button>
                              ) : (
                                // File card with icon
                                <div className="h-full flex flex-col items-center justify-center p-3 text-center">
                                  <svg
                                    xmlns="http://www.w3.org/2000/svg"
                                    viewBox="0 0 24 24"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    className="w-10 h-10 mb-2 text-white"
                                  >
                                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                                    <polyline points="14 2 14 8 20 8"></polyline>
                                  </svg>
                                  <span
                                    className="text-xs font-medium text-foreground truncate w-full px-1 mb-1"
                                    title={fileName}
                                  >
                                    {fileName}
                                  </span>
                                  <div className="flex items-center gap-1.5">
                                    <span className="text-xs font-bold px-1.5 py-0.5 rounded bg-muted text-white">
                                      {fileExt?.toUpperCase().slice(0, 4)}
                                    </span>
                                    {fileSizeKB > 0 && (
                                      <span className="text-xs text-muted-foreground">
                                        {fileSizeKB}KB
                                      </span>
                                    )}
                                  </div>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {/* Text content */}
                    {message.content && (
                      <p
                        data-testid={`message-content-${message.role}`}
                        className="text-sm leading-relaxed whitespace-pre-wrap break-words cursor-pointer rounded px-2 py-1 -mx-2 -my-1 transition-colors overflow-wrap break-word"
                        onClick={() => onEditAndRerun && handleStartEdit()}
                        title="Click to edit and rerun from here"
                      >
                        {String(message.content || "")}
                      </p>
                    )}
                  </div>
                )
              ) : (
                <div className="relative">
                  <div
                    data-testid={`message-content-${message.role}`}
                    data-streaming={message.isThinking ? "true" : "false"}
                    data-error={message.isError ? "true" : "false"}
                    className="text-sm leading-relaxed prose prose-sm dark:prose-invert max-w-none break-words overflow-wrap break-word transition-opacity duration-200 ease-out"
                    style={{
                      animation: message.isThinking
                        ? "none"
                        : "fadeIn 0.3s ease-out",
                      willChange: message.isThinking
                        ? "contents, opacity"
                        : "auto",
                      backfaceVisibility: "hidden",
                      transform: "translateZ(0)",
                      fontSize: "calc(0.875rem * var(--font-scale-chat, 1))",
                    }}
                  >
                    {message.content && typeof message.content === "string" ? (
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={markdownComponents}
                      >
                        {stripMarkdownEnvelope(message.content)}
                      </ReactMarkdown>
                    ) : message.isThinking ? (
                      /* Cursor piscante pré-1º token — indica que o modelo está gerando */
                      <span
                        className="inline-block w-[2px] h-[1em] bg-primary align-middle"
                        aria-hidden="true"
                        style={{ animation: "blink 1s step-end infinite" }}
                      />
                    ) : null}
                  </div>

                  {/* C.28 — RAG citation list */}
                  {message.ragCitations && message.ragCitations.length > 0 && (
                    <RagCitationList citations={message.ragCitations} />
                  )}
                </div>
              )}
            </div>

            {/* Botões de ação para mensagens do usuário (copy + rewind) */}
            {message.role === "user" && !isEditing && (
              <>
                <TooltipProvider delayDuration={300}>
                  <div className="flex justify-end items-center gap-0.5 mt-0.5 opacity-0 group-hover/message:opacity-100 transition-opacity duration-150">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => onCopy(message.content, message.id)}
                          className="h-5 w-5 text-muted-foreground hover:text-foreground"
                          aria-label={m.chat_copy()}
                        >
                          {copiedId === message.id ? (
                            <Check className="w-2.5 h-2.5" />
                          ) : (
                            <Copy className="w-2.5 h-2.5" />
                          )}
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>
                        {copiedId === message.id
                          ? m.chat_copied()
                          : m.chat_copy()}
                      </TooltipContent>
                    </Tooltip>
                    {workspaceId && humanMessageIndex !== undefined && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setRewindOpen(true)}
                            className="h-5 w-5 text-muted-foreground hover:text-foreground"
                            aria-label={m.chat_rewind()}
                          >
                            <RotateCcw className="w-2.5 h-2.5" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>{m.chat_rewind()}</TooltipContent>
                      </Tooltip>
                    )}
                  </div>
                </TooltipProvider>

                {workspaceId && humanMessageIndex !== undefined && (
                  <Dialog open={rewindOpen} onOpenChange={setRewindOpen}>
                    <DialogContent>
                      <DialogHeader>
                        <DialogTitle>{m.chat_rewind_title()}</DialogTitle>
                        <DialogDescription>
                          {m.chat_rewind_desc()}
                        </DialogDescription>
                      </DialogHeader>
                      <DialogFooter>
                        <Button
                          variant="outline"
                          onClick={() => setRewindOpen(false)}
                          disabled={rewinding}
                        >
                          {m.workbench_files_cancel()}
                        </Button>
                        <Button
                          variant="destructive"
                          onClick={handleRewind}
                          disabled={rewinding}
                        >
                          {rewinding ? (
                            <RefreshCw className="w-3 h-3 mr-1 animate-spin" />
                          ) : (
                            <RotateCcw className="w-3 h-3 mr-1" />
                          )}
                          {m.chat_rewind_confirm()}
                        </Button>
                      </DialogFooter>
                    </DialogContent>
                  </Dialog>
                )}
              </>
            )}

            <Dialog
              open={lightboxImage !== null}
              onOpenChange={(v) => !v && setLightboxImage(null)}
            >
              <DialogContent className="max-w-[90vw] max-h-[90vh] w-fit p-2 bg-background/95">
                <DialogHeader className="sr-only">
                  <DialogTitle>
                    {lightboxImage?.name ?? m.chat_image_lightbox_title()}
                  </DialogTitle>
                  <DialogDescription>
                    {m.chat_image_lightbox_title()}
                  </DialogDescription>
                </DialogHeader>
                {lightboxImage && (
                  <div className="relative flex items-center justify-center">
                    <img
                      src={lightboxImage.src}
                      alt={lightboxImage.name}
                      className="max-h-[85vh] max-w-[85vw] object-contain rounded"
                    />
                    <div className="absolute top-2 right-2 flex gap-1">
                      <a
                        href={lightboxImage.src}
                        download={lightboxImage.name}
                        className="p-1.5 rounded-md bg-black/60 text-white hover:bg-black/80 transition-colors"
                        title={m.chat_image_lightbox_download()}
                      >
                        <Download className="w-4 h-4" />
                      </a>
                      <button
                        type="button"
                        onClick={() => setLightboxImage(null)}
                        className="p-1.5 rounded-md bg-black/60 text-white hover:bg-black/80 transition-colors"
                        title={m.workbench_files_cancel()}
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                )}
              </DialogContent>
            </Dialog>

            {message.role === "assistant" && (
              <>
                <TooltipProvider delayDuration={300}>
                  <div className="flex items-center justify-between mt-0.5 opacity-0 group-hover/message:opacity-100 transition-opacity duration-150">
                    <div className="flex gap-0.5 items-center flex-wrap">
                      {/* M5 — Botão de retry para mensagens de erro */}
                      {message.isError && onRetry && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={onRetry}
                              className="h-6 w-6 text-destructive hover:text-destructive"
                              aria-label={m.chat_retry()}
                            >
                              <RefreshCw className="w-3 h-3" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>{m.chat_retry()}</TooltipContent>
                        </Tooltip>
                      )}

                      {!message.isThinking && !message.isError && (
                        <>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() =>
                                  onCopy(message.content, message.id)
                                }
                                className="h-6 w-6 text-muted-foreground hover:text-foreground"
                                aria-label={m.chat_copy()}
                              >
                                {copiedId === message.id ? (
                                  <Check className="w-3 h-3" />
                                ) : (
                                  <Copy className="w-3 h-3" />
                                )}
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>
                              {copiedId === message.id
                                ? m.chat_copied()
                                : m.chat_copy()}
                            </TooltipContent>
                          </Tooltip>

                          {speech.supported && (
                            <>
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-6 w-6 text-muted-foreground hover:text-foreground"
                                    onClick={
                                      speech.state === "idle"
                                        ? speech.speak
                                        : speech.state === "paused"
                                          ? speech.resume
                                          : speech.pause
                                    }
                                    aria-label={
                                      speech.state === "speaking"
                                        ? m.message_tts_pause()
                                        : speech.state === "paused"
                                          ? m.message_tts_resume()
                                          : m.message_tts_listen()
                                    }
                                    aria-pressed={speech.state === "speaking"}
                                  >
                                    {speech.state === "speaking" ? (
                                      <Pause className="w-3 h-3" />
                                    ) : speech.state === "paused" ? (
                                      <Play className="w-3 h-3" />
                                    ) : (
                                      <Volume2 className="w-3 h-3" />
                                    )}
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>
                                  {speech.state === "speaking"
                                    ? m.message_tts_pause()
                                    : speech.state === "paused"
                                      ? m.message_tts_resume()
                                      : m.message_tts_listen()}
                                </TooltipContent>
                              </Tooltip>
                              {speech.state !== "idle" && (
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-6 w-6 text-muted-foreground hover:text-foreground"
                                  onClick={speech.stop}
                                  aria-label={m.message_tts_stop()}
                                >
                                  <Square className="w-3 h-3" />
                                </Button>
                              )}
                            </>
                          )}

                          {isLastAssistant && (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  onClick={onRegenerate}
                                  disabled={isRegenerating}
                                  className="h-6 w-6 text-muted-foreground hover:text-foreground"
                                  aria-label={m.chat_regenerate()}
                                >
                                  <RefreshCw
                                    className={`w-3 h-3 ${isRegenerating ? "animate-spin" : ""}`}
                                  />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>
                                {m.chat_regenerate()}
                              </TooltipContent>
                            </Tooltip>
                          )}
                        </>
                      )}

                      {!message.isThinking && message.runId && (
                        <>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() =>
                                  onFeedback(
                                    message.id,
                                    "positive",
                                    feedbackComment[message.id],
                                  )
                                }
                                aria-pressed={message.feedback === "positive"}
                                aria-label={m.chat_feedback_good()}
                                className={`h-6 w-6 ${message.feedback === "positive" ? "text-primary" : "text-muted-foreground hover:text-foreground"}`}
                              >
                                <ThumbsUp
                                  className="w-3 h-3"
                                  aria-hidden="true"
                                  fill={
                                    message.feedback === "positive"
                                      ? "currentColor"
                                      : "none"
                                  }
                                />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>
                              {m.chat_feedback_good()}
                            </TooltipContent>
                          </Tooltip>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() =>
                                  onFeedback(
                                    message.id,
                                    "negative",
                                    feedbackComment[message.id],
                                  )
                                }
                                aria-pressed={message.feedback === "negative"}
                                aria-label={m.chat_feedback_bad()}
                                className={`h-6 w-6 ${message.feedback === "negative" ? "text-primary" : "text-muted-foreground hover:text-foreground"}`}
                              >
                                <ThumbsDown
                                  className="w-3 h-3"
                                  aria-hidden="true"
                                  fill={
                                    message.feedback === "negative"
                                      ? "currentColor"
                                      : "none"
                                  }
                                />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>
                              {m.chat_feedback_bad()}
                            </TooltipContent>
                          </Tooltip>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => onToggleComment(message.id)}
                                className="h-6 w-6 text-muted-foreground hover:text-foreground"
                                aria-label={m.chat_feedback_comment()}
                              >
                                <MessageSquare className="w-3 h-3" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>
                              {m.chat_feedback_comment()}
                            </TooltipContent>
                          </Tooltip>
                        </>
                      )}
                    </div>

                    {/* Metadata (timestamp + duração) ao lado dos botões */}
                    {!message.isThinking &&
                      message.thinkingDuration != null && (
                        <span
                          className="text-[10px] text-muted-foreground/50 font-mono tabular-nums"
                          title={new Date(message.timestamp).toLocaleString()}
                        >
                          {formatDistanceToNow(new Date(message.timestamp), {
                            addSuffix: true,
                            locale: DATE_FNS_LOCALES[uiLang] ?? enUS,
                          })}
                          {" · "}
                          {(message.thinkingDuration / 1000).toFixed(1)}s
                          {modelId &&
                            message.usageMetadata?.input_tokens != null &&
                            message.usageMetadata?.output_tokens != null &&
                            ` · ${formatCost(estimateCost(modelId, message.usageMetadata.input_tokens, message.usageMetadata.output_tokens ?? 0))}`}
                        </span>
                      )}
                  </div>
                </TooltipProvider>

                {showCommentInput === message.id && (
                  <div className="mt-2 w-full">
                    <Textarea
                      value={feedbackComment[message.id] || ""}
                      onChange={(e) => {
                        setFeedbackComment((prev) => ({
                          ...prev,
                          [message.id]: e.target.value,
                        }));
                      }}
                      placeholder="Add feedback about this response..."
                      className="min-h-[60px] text-xs"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          if (
                            feedbackComment[message.id]?.trim() &&
                            message.feedback
                          ) {
                            onSubmitComment(message.id);
                          }
                        } else if (e.key === "Escape") {
                          onCancelComment(message.id);
                        }
                      }}
                    />
                    {!message.feedback && (
                      <p className="text-[10px] text-muted-foreground mt-1">
                        Select thumbs up or down before submitting
                      </p>
                    )}
                  </div>
                )}

                {showToolCalls &&
                  message.toolCalls &&
                  message.toolCalls.length > 0 && (
                    <details className="mt-3 group" open={!!message.isThinking}>
                      <summary className="cursor-pointer list-none flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground transition-colors select-none">
                        <span className="group-open:hidden">▶</span>
                        <span className="hidden group-open:inline">▾</span>
                        <span>
                          {message.toolCalls.length === 1
                            ? "1 ação"
                            : `${message.toolCalls.length} ações`}
                        </span>
                      </summary>
                      <div className="mt-2 space-y-2">
                        {message.toolCalls.map((tool) => (
                          <ToolCallRenderer
                            key={tool.id}
                            tool={tool}
                            isStreaming={message.isThinking}
                          />
                        ))}
                      </div>
                    </details>
                  )}

                {message.isThinking && (
                  <AgentStatusLine activeTool={message.activeTool} />
                )}

                {message.checkpointId === "partial" && !message.isThinking && (
                  <div className="mt-3 flex items-center gap-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive border border-destructive/20">
                    <AlertTriangle className="w-4 h-4 shrink-0" />
                    <span>
                      Esta resposta foi recuperada parcialmente. A conexão caiu
                      ou a geração falhou antes do assistente concluir o
                      pensamento.
                    </span>
                  </div>
                )}

                {message.subgraphOutputs &&
                  message.subgraphOutputs.length > 0 && (
                    <div className="mt-3">
                      <details className="group">
                        <summary className="cursor-pointer text-xs font-semibold text-muted-foreground hover:text-foreground transition-colors flex items-center gap-2">
                          <span>
                            {m.chat_subagent_outputs()} (
                            {message.subgraphOutputs.length})
                          </span>
                          <span className="text-[10px] opacity-50">
                            {m.chat_subagent_expand()}
                          </span>
                        </summary>
                        <div className="mt-2 space-y-2">
                          {message.subgraphOutputs.map((subgraph, idx) => (
                            <details
                              key={`${subgraph.name}-${idx}`}
                              className="px-3 py-2 rounded-lg border border-primary/30 bg-primary/5"
                            >
                              <summary className="cursor-pointer flex items-center gap-2 text-xs hover:opacity-80">
                                <Settings className="w-3 h-3 text-primary" />
                                <span className="font-semibold text-primary">
                                  {subgraph.name}
                                </span>
                                {subgraph.isStreaming && (
                                  <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                                    <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                                    {m.chat_subagent_running()}
                                  </span>
                                )}
                                {subgraph.isComplete && (
                                  <span className="text-[10px] text-green-600 dark:text-green-400">
                                    {m.chat_subagent_complete()}
                                  </span>
                                )}
                              </summary>
                              {subgraph.output ? (
                                <div className="mt-2 text-xs font-mono text-muted-foreground">
                                  <pre className="whitespace-pre-wrap break-words text-[10px] max-h-60 overflow-y-auto">
                                    {subgraph.output}
                                  </pre>
                                </div>
                              ) : (
                                <div className="mt-2 text-[10px] text-muted-foreground italic">
                                  {m.chat_subagent_waiting()}
                                </div>
                              )}
                            </details>
                          ))}
                        </div>
                      </details>
                    </div>
                  )}

                {/* E2 — Painel HITL (aprovação humana antes de ação destrutiva) */}
                {message.hitlPending && onHitlDecision && (
                  <HITLPanel
                    messageId={message.id}
                    pending={message.hitlPending}
                    threadId={threadId}
                    onDecision={onHitlDecision}
                  />
                )}
              </>
            )}
          </div>
        </div>
      </>
    );
  },
  (prevProps, nextProps) => {
    // Custom comparison: skip re-render only if props affecting THIS message are unchanged
    // Message content/object changed - always re-render (e.g., during streaming)
    if (prevProps.message !== nextProps.message) {
      return false;
    }

    // copiedId changed - only re-render if it affects this message
    const copiedIdAffectsThis =
      prevProps.copiedId !== nextProps.copiedId &&
      (prevProps.copiedId === prevProps.message.id ||
        nextProps.copiedId === nextProps.message.id);

    // showCommentInput changed - only re-render if it affects this message
    const commentInputAffectsThis =
      prevProps.showCommentInput !== nextProps.showCommentInput &&
      (prevProps.showCommentInput === prevProps.message.id ||
        nextProps.showCommentInput === nextProps.message.id);

    // feedbackComment changed for THIS message
    const feedbackCommentChanged =
      prevProps.feedbackComment[prevProps.message.id] !==
      nextProps.feedbackComment[nextProps.message.id];

    // Other props that affect rendering
    const otherPropsChanged =
      prevProps.showToolCalls !== nextProps.showToolCalls ||
      prevProps.isRegenerating !== nextProps.isRegenerating ||
      prevProps.isLastAssistant !== nextProps.isLastAssistant ||
      prevProps.workspaceId !== nextProps.workspaceId ||
      prevProps.humanMessageIndex !== nextProps.humanMessageIndex;

    // Re-render if any relevant prop changed
    if (
      copiedIdAffectsThis ||
      commentInputAffectsThis ||
      feedbackCommentChanged ||
      otherPropsChanged
    ) {
      return false;
    }

    // Function references - if they changed, we need to re-render (shouldn't happen with useCallback)
    const functionsChanged =
      prevProps.onCopy !== nextProps.onCopy ||
      prevProps.onRegenerate !== nextProps.onRegenerate ||
      prevProps.onEditAndRerun !== nextProps.onEditAndRerun ||
      prevProps.onFeedback !== nextProps.onFeedback ||
      prevProps.onSubmitComment !== nextProps.onSubmitComment ||
      prevProps.onCancelComment !== nextProps.onCancelComment ||
      prevProps.onToggleComment !== nextProps.onToggleComment ||
      prevProps.setFeedbackComment !== nextProps.setFeedbackComment;

    if (functionsChanged) {
      return false;
    }

    // All props that matter for this message are unchanged - skip re-render
    return true;
  },
);
