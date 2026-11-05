"use client";

import * as React from "react";
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Info,
  LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

type CalloutType = "info" | "warning" | "error" | "success";

interface CalloutProps extends React.HTMLAttributes<HTMLDivElement> {
  type?: CalloutType;
  icon?: LucideIcon;
  title?: string;
  children: React.ReactNode;
}

const ICONS: Record<CalloutType, LucideIcon> = {
  info: Info,
  warning: AlertTriangle,
  error: AlertCircle,
  success: CheckCircle2,
};

const STYLES: Record<
  CalloutType,
  { wrapper: string; icon: string; title: string }
> = {
  info: {
    wrapper:
      "border-blue-500/30 bg-blue-500/5 text-blue-900 dark:text-blue-100",
    icon: "text-blue-600 dark:text-blue-400",
    title: "font-semibold text-blue-900 dark:text-blue-100",
  },
  warning: {
    wrapper:
      "border-amber-500/30 bg-amber-500/5 text-amber-900 dark:text-amber-100",
    icon: "text-amber-600 dark:text-amber-400",
    title: "font-semibold text-amber-900 dark:text-amber-100",
  },
  error: {
    wrapper: "border-red-500/30 bg-red-500/5 text-red-900 dark:text-red-100",
    icon: "text-red-600 dark:text-red-400",
    title: "font-semibold text-red-900 dark:text-red-100",
  },
  success: {
    wrapper:
      "border-emerald-500/30 bg-emerald-500/5 text-emerald-900 dark:text-emerald-100",
    icon: "text-emerald-600 dark:text-emerald-400",
    title: "font-semibold text-emerald-900 dark:text-emerald-100",
  },
};

export const Callout = React.forwardRef<HTMLDivElement, CalloutProps>(
  (
    { type = "info", icon: Icon, title, className, children, ...props },
    ref,
  ) => {
    const IconComponent = Icon || ICONS[type];
    const styles = STYLES[type];

    return (
      <div
        ref={ref}
        className={cn(
          "relative w-full rounded-lg border px-4 py-3",
          styles.wrapper,
          className,
        )}
        role={type === "error" || type === "warning" ? "alert" : "note"}
        {...props}
      >
        <div className="flex gap-3">
          <IconComponent
            className={cn("mt-0.5 h-5 w-5 shrink-0", styles.icon)}
          />
          <div className="flex-1 min-w-0">
            {title && <p className={styles.title}>{title}</p>}
            <div className="text-sm opacity-90 [&>p]:m-0 [&>p+p]:mt-2">
              {children}
            </div>
          </div>
        </div>
      </div>
    );
  },
);
Callout.displayName = "Callout";
