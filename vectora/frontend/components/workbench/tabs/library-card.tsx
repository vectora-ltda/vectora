"use client";

import type { HTMLAttributes, KeyboardEvent, ReactNode } from "react";

import { cn } from "@/lib/utils";

interface LibraryCardProps {
  icon: ReactNode;
  title: ReactNode;
  description: ReactNode;
  action?: ReactNode;
  tags?: ReactNode;
  footer?: ReactNode;
  children?: ReactNode;
  className?: string;
  onClick?: () => void;
}

/** Shared card geometry used by the three Library catalogs. */
export function LibraryCard({
  icon,
  title,
  description,
  action,
  tags,
  footer,
  children,
  className,
  onClick,
}: LibraryCardProps) {
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!onClick || event.currentTarget !== event.target) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onClick();
  };

  return (
    <div
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={handleKeyDown}
      className={cn(
        "flex items-start gap-2 rounded-lg [border-radius:12px] border border-[#2a2a2a] bg-[#1a1a1a] p-2",
        onClick &&
          "cursor-pointer transition-colors hover:border-[#3a3a3a] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/60",
        className,
      )}
    >
      <div className="flex size-[26px] shrink-0 items-center justify-center rounded-md bg-[#262626] p-1.5 text-muted-foreground">
        {icon}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 items-start justify-between gap-2">
          <h3 className="min-w-0 flex-1 text-sm font-medium leading-5 text-[#d4d4d4]">
            {title}
          </h3>
          {action && (
            <div
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
            >
              {action}
            </div>
          )}
        </div>
        <p className="line-clamp-4 text-xs leading-4 text-muted-foreground">
          {description}
        </p>
        {tags && (
          <div className="flex min-w-0 flex-wrap items-center gap-1.5 pt-0.5">
            {tags}
          </div>
        )}
        {footer}
        {children}
      </div>
    </div>
  );
}

export function LibraryTag({
  children,
  verified = false,
  className,
  ...props
}: {
  children: ReactNode;
  verified?: boolean;
  className?: string;
} & HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex h-4 max-w-full items-center rounded-md px-1.5 text-[10px] font-semibold leading-[14px]",
        verified
          ? "bg-[#d4d4d4] text-[#2a2a2a]"
          : "bg-[#2a2a2a] text-[#d4d4d4]",
        className,
      )}
      {...props}
    >
      <span className="truncate">{children}</span>
    </span>
  );
}
