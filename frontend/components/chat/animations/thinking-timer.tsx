"use client";

import { useEffect, useState } from "react";

interface ThinkingTimerProps {
  startTime?: number;
  duration?: number;
  isThinking: boolean;
}

function formatTime(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}m ${remainingSeconds}s`;
}

export function ThinkingTimer({
  startTime,
  duration,
  isThinking,
}: ThinkingTimerProps) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (isThinking && startTime) {
      // Sincroniza com o relógio do sistema (Date.now) via intervalo — não
      // é estado derivado de props, precisa do efeito pra manter o timer.
      // oxlint-disable-next-line react/set-state-in-effect
      setElapsed(0);
      const interval = setInterval(() => {
        setElapsed(Date.now() - startTime);
      }, 100);
      return () => clearInterval(interval);
    } else if (duration !== undefined) {
      setElapsed(duration);
    }
  }, [isThinking, startTime, duration]);

  return (
    <span className="text-muted-foreground font-mono text-[10px]">
      {formatTime(elapsed)}
    </span>
  );
}
