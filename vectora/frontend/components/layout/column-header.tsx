import type { ReactNode } from "react";

/** Shared geometry for titles rendered inside any session column. */
export function ColumnHeader({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      data-testid="column-header"
      className={`flex h-[var(--app-header-height)] min-h-[var(--app-header-height)] shrink-0 items-center border-b border-border/60 bg-sidebar ${className}`}
    >
      {children}
    </div>
  );
}
