/**
 * Message Types
 *
 * Type definitions for chat messages and related structures.
 */

import type { ToolCall } from "./tools";
import type { SubgraphOutput } from "./tools";
import type { UsageMetadata } from "./metadata";
import type { ImageAttachment } from "./images";

/**
 * Represents a chat message from either user or assistant.
 * Contains metadata for streaming, tool calls, feedback, and tracing.
 */
export interface Message {
  // Core properties
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;

  // Image attachments
  images?: ImageAttachment[];

  // Tool execution
  toolCalls?: ToolCall[];
  subgraphOutputs?: SubgraphOutput[];

  // Thinking/streaming state
  isThinking?: boolean;
  thinkingSteps?: string[];
  thinkingStartTime?: number;
  thinkingDuration?: number;

  /** Label semântico do nó atualmente em execução */
  currentNodeLabel?: string;
  /** Durações por nó, acumuladas durante o stream */
  nodeDurations?: { node: string; label: string; duration_ms: number }[];
  /** Tool ativa no momento — limpo quando tool termina */
  activeTool?: { name: string; argsPreview: string; elapsedMs?: number } | null;

  // Rastreio de execução
  runId?: string;
  shareUrl?: string; // URL pública de compartilhamento do trace
  usageMetadata?: UsageMetadata;

  // User feedback
  feedback?: "positive" | "negative" | null;
  feedbackId?: string;
  feedbackComment?: string;

  // Interruption tracking
  wasInterrupted?: boolean;

  /** Checkpoint pai desta mensagem (ver HistoryMessage.checkpoint_id no
   * backend) — alvo de fork ao editar esta mensagem ou, se for a última
   * resposta do assistente, ao regenerá-la. */
  checkpointId?: string;

  /** Mensagem é uma falha de stream — exibe botão de retry */
  isError?: boolean;

  /** Fontes RAG retornadas durante a resposta, para renderizar referências [N]. */
  ragCitations?: Array<{ index: number; source: string; chunk: string }>;

  /** Preenchido quando o stream pausa para aprovação humana. */
  hitlPending?: {
    toolName: string;
    argsJson: string;
    interruptId: string;
    /** Razão para a ação (exibida no painel). */
    reasoning?: string;
    /** Preview diff unified para file_write/file_edit. */
    diffPreview?: string;
    /** Caminhos de arquivo afetados. */
    affectedPaths?: string[];
    /** Modo de permissão ativo (default/yolo/…). */
    permissionMode?: string;
    options?: Array<{ label: string; value: string }>;
    priority?: number;
    expiresAt?: string | null;
  };
}
