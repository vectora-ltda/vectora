import type { ReactNode } from "react";
import { useReducedMotion } from "motion/react";
import { SideColumn } from "@/components/layout/side-column";
import { MOTION_INSTANT } from "@/lib/motion/transitions";
import { WORKBENCH_WIDTH_TRANSITION } from "@/lib/layout/workbench-geometry";

export type ShellColumnVisibility = "visible" | "collapsed" | "hidden";

interface ShellColumnStateBase {
  visibility?: ShellColumnVisibility;
  width?: number;
  minWidth?: number;
  maxWidth?: number;
  resize?: ShellColumnResize;
  label: string;
}

export interface ShellColumnResize {
  ariaLabel: string;
  value: number;
  min: number;
  max: number;
  onKeyDown: React.KeyboardEventHandler<HTMLDivElement>;
  onPointerDown: React.PointerEventHandler<HTMLDivElement>;
  onPointerMove: React.PointerEventHandler<HTMLDivElement>;
  onPointerUp: React.PointerEventHandler<HTMLDivElement>;
  onPointerCancel: React.PointerEventHandler<HTMLDivElement>;
}

export type ShellColumnState = ShellColumnStateBase &
  (
    | {
        visibility?: Exclude<ShellColumnVisibility, "collapsed">;
        onExpand?: () => void;
        expandLabel?: string;
      }
    | {
        visibility: "collapsed";
        onExpand: () => void;
        expandLabel?: string;
      }
  );

export interface ShellCenterColumnState {
  label: string;
}

export interface ThreeColumnShellProps {
  centerHeader: ReactNode;
  left: ReactNode | null;
  center: ReactNode;
  right: ReactNode | null;
  direction?: "ltr" | "rtl";
  showRight?: boolean;
  columns: {
    left: ShellColumnState;
    center?: ShellCenterColumnState;
    right: ShellColumnState;
  };
  className?: string;
}

/** Stable geometry for every mode. The application header belongs only here. */
export function ThreeColumnShell({
  centerHeader,
  left,
  center,
  right,
  direction = "ltr",
  showRight = true,
  columns,
  className,
}: ThreeColumnShellProps) {
  const reducedMotion = useReducedMotion();
  const workbenchTransition = reducedMotion
    ? MOTION_INSTANT
    : WORKBENCH_WIDTH_TRANSITION;
  const rightVisibility = showRight
    ? (columns.right.visibility ?? "visible")
    : "hidden";
  return (
    <div
      className={`relative flex flex-1 min-h-0 min-w-0 overflow-hidden ${className ?? ""}`}
    >
      <div
        className={`flex flex-1 min-h-0 min-w-0 overflow-hidden pt-0 ${direction === "rtl" ? "flex-row-reverse" : ""}`}
      >
        <SideColumn
          side={direction === "rtl" ? "right" : "left"}
          column={columns.left}
          content={left}
          transition={workbenchTransition}
        />
        <main
          aria-label={columns.center?.label ?? "Conteúdo principal"}
          data-column-visibility="visible"
          className="flex flex-[1_1_0%] min-h-0 min-w-0 flex-col overflow-hidden"
        >
          <div
            data-testid="shell-header-slot"
            className="z-20 h-[var(--app-header-height)] shrink-0"
          >
            {centerHeader}
          </div>
          <div className="flex flex-1 min-h-0 min-w-0 overflow-hidden">
            {center}
          </div>
        </main>
        <SideColumn
          side={direction === "rtl" ? "left" : "right"}
          column={
            {
              ...columns.right,
              visibility: rightVisibility,
            } as ShellColumnState
          }
          content={right}
          transition={workbenchTransition}
        />
      </div>
    </div>
  );
}
