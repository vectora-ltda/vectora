/**
 * Tool-Related Types
 *
 * Type definitions for tool calls and subgraph execution.
 */

import type { RenderHint, ToolCategory } from "./render";

export type { RenderHint, ToolCategory } from "./render";

/**
 * Representa uma tool call feita pelo assistente.
 * Os campos `renderHint`, `category`, `destructive` e `icon` vêm do
 * metadata Python via SSE e permitem a renderização schema-driven.
 */
export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, any>;
  output?: any;
  renderHint?: RenderHint;
  category?: ToolCategory;
  destructive?: boolean;
  icon?: string;
  /** Duração em ms — preenchido pelo tool_activity(end) SSE */
  elapsedMs?: number;
  isError?: boolean;
  /** Linhas de output ao vivo (evento `terminal_line`) — só pra `terminal`,
   * enquanto o comando ainda roda e `output` ainda não chegou. */
  liveOutputLines?: string[];
}

/**
 * Represents the output of a subagent/subgraph execution.
 * Used to display parallel task execution in the UI.
 */
export interface SubgraphOutput {
  name: string;
  output: string;
  timestamp: number;
  toolCallId?: string;
  isStreaming?: boolean;
  isComplete?: boolean;
}
