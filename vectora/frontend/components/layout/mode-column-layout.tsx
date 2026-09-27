import type { ReactNode } from "react";
import {
  ThreeColumnShell,
  type ShellColumnState,
} from "@/components/layout/three-column-shell";
import { m } from "@/lib/paraglide/messages";

type ColumnConfig = Omit<ShellColumnState, "label">;

export interface ModeColumnLayoutProps {
  header: ReactNode;
  left: ReactNode;
  center: ReactNode;
  right?: ReactNode;
  showRight?: boolean;
  direction?: "ltr" | "rtl";
  leftColumn?: ColumnConfig;
  rightColumn?: ColumnConfig;
  className?: string;
}

/**
 * Shared desktop shell for the three Vectora modes.
 *
 * The center column is the only owner of the application header. Each mode
 * supplies independent slots below it, so changing mode only remounts the
 * content assigned to a column and never moves the header over a sidebar.
 */
export function ModeColumnLayout({
  header,
  left,
  center,
  right,
  showRight = true,
  direction = "ltr",
  leftColumn,
  rightColumn,
  className,
}: ModeColumnLayoutProps) {
  return (
    <ThreeColumnShell
      centerHeader={header}
      left={left}
      center={
        <div className="flex w-full min-h-0 min-w-0 flex-1 overflow-hidden">
          {center}
        </div>
      }
      right={right}
      direction={direction}
      columns={{
        left: {
          kind: "workbench",
          label: m.layout_workbench_column(),
          ...leftColumn,
        } as ShellColumnState,
        center: { label: "Conteúdo principal" },
        right: {
          label: "Chat",
          ...rightColumn,
          visibility:
            !showRight || !right
              ? "hidden"
              : (rightColumn?.visibility ?? "visible"),
        } as ShellColumnState,
      }}
      className={className}
    />
  );
}
