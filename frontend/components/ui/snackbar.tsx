"use client";

import * as React from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";
import { m } from "@/lib/paraglide/messages";

interface SnackbarProps {
  message: string;
  onDismiss?: () => void;
  duration?: number;
  action?: {
    label: string;
    onClick: () => void;
  };
  variant?: "default" | "success" | "error" | "warning";
  className?: string;
}

export function Snackbar({
  message,
  onDismiss,
  duration = 4000,
  action,
  variant = "default",
  className,
}: SnackbarProps) {
  React.useEffect(() => {
    if (duration <= 0 || !onDismiss) return;
    const timer = window.setTimeout(onDismiss, duration);
    return () => window.clearTimeout(timer);
  }, [duration, onDismiss]);

  const variantStyles = {
    default: "bg-foreground text-background",
    success: "bg-emerald-600 text-white",
    error: "bg-red-600 text-white",
    warning: "bg-amber-600 text-white",
  };

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "safe-area-bottom-snackbar safe-area-right-snackbar pointer-events-auto fixed bottom-4 right-4 z-50 flex items-center gap-3 rounded-md px-4 py-3 text-sm shadow-lg backdrop-blur-sm animate-in slide-in-from-bottom-5 fade-in-0",
        variantStyles[variant],
        className,
      )}
    >
      <span className="flex-1">{message}</span>
      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className="shrink-0 font-medium underline underline-offset-2 hover:no-underline opacity-90 hover:opacity-100"
        >
          {action.label}
        </button>
      )}
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label={m.workbench_close()}
          className="shrink-0 rounded p-0.5 opacity-70 hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-current focus:ring-offset-2"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      )}
    </div>
  );
}
