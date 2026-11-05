"use client";

import { Loader2 } from "lucide-react";

interface ActiveTool {
  name: string;
  argsPreview: string;
  elapsedMs?: number;
}

interface AgentStatusLineProps {
  activeTool: ActiveTool | null | undefined;
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function AgentStatusLine({ activeTool }: AgentStatusLineProps) {
  if (!activeTool) return null;

  const { name, argsPreview, elapsedMs } = activeTool;
  const isDone = elapsedMs !== undefined;

  return (
    <div
      data-testid="agent-status-line"
      role="status"
      aria-live="polite"
      className="flex items-center gap-1.5 text-xs text-muted-foreground py-0.5 px-1"
    >
      {isDone ? (
        <span className="text-green-500 text-[10px]">✓</span>
      ) : (
        <Loader2 className="w-3 h-3 animate-spin shrink-0" />
      )}
      <span className="font-mono">{name}</span>
      {argsPreview && (
        <span className="text-muted-foreground/70 truncate max-w-[200px]">
          — {argsPreview}
        </span>
      )}
      {isDone && (
        <span className="ml-auto text-muted-foreground/50 tabular-nums">
          {formatElapsed(elapsedMs)}
        </span>
      )}
    </div>
  );
}
