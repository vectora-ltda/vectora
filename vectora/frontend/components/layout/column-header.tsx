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
      className={`flex h-16 min-h-16 shrink-0 items-center border-b border-border/60 bg-sidebar ${className}`}
    >
      {children}
    </div>
  );
}
