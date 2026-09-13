import type { ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import { RailToggleButton } from "@/components/layout/rail-toggle-button";
import { MOTION_INSTANT } from "@/lib/motion/transitions";
import {
  WORKBENCH_RAIL_WIDTH,
  WORKBENCH_WIDTH_TRANSITION,
} from "@/lib/layout/workbench-geometry";

export type ShellColumnVisibility = "visible" | "collapsed" | "hidden";

interface ShellColumnStateBase {
  visibility?: ShellColumnVisibility;
  width?: number;
  minWidth?: number;
  maxWidth?: number;
  label: string;
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

function columnStyle(
  column: ShellColumnState,
): React.CSSProperties | undefined {
  if (
    column.width == null &&
    column.minWidth == null &&
    column.maxWidth == null
  ) {
    return undefined;
  }
  return {
    width: column.width,
    minWidth: column.minWidth,
    maxWidth: column.maxWidth,
  };
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
  const leftVisibility = columns.left.visibility ?? "visible";
  const rightVisibility = showRight
    ? (columns.right.visibility ?? "visible")
    : "hidden";
  return (
    <div
      className={`relative flex flex-1 min-h-0 min-w-0 overflow-hidden ${className ?? ""}`}
    >
      <div
        data-testid="shell-header-slot"
        className="pointer-events-none absolute inset-x-0 top-0 z-20 h-[var(--app-header-height)]"
      >
        <div className="pointer-events-auto mx-auto h-full w-full max-w-[900px]">
          {centerHeader}
        </div>
      </div>
      <div
        className={`flex flex-1 min-h-0 min-w-0 overflow-hidden pt-0 ${direction === "rtl" ? "flex-row-reverse" : ""}`}
      >
        {leftVisibility !== "hidden" && (
          <motion.aside
            animate={
              columns.left.label === "Workbench"
                ? {
                    width:
                      leftVisibility === "collapsed"
                        ? WORKBENCH_RAIL_WIDTH
                        : columns.left.width,
                  }
                : undefined
            }
            transition={workbenchTransition}
            aria-label={columns.left.label}
            data-column-visibility={leftVisibility}
            className="flex shrink-0 min-h-0 min-w-0 overflow-hidden"
            style={
              leftVisibility === "collapsed"
                ? { width: WORKBENCH_RAIL_WIDTH }
                : columnStyle(columns.left)
            }
          >
            {leftVisibility === "collapsed" ? (
              <div className="flex h-full w-full items-start justify-center pt-3">
                <RailToggleButton
                  side={direction === "rtl" ? "right" : "left"}
                  ariaLabel={
                    columns.left.expandLabel ?? `Expandir ${columns.left.label}`
                  }
                  ariaExpanded={false}
                  onClick={columns.left.onExpand}
                />
              </div>
            ) : (
              left
            )}
          </motion.aside>
        )}
        <main
          aria-label={columns.center?.label ?? "Conteúdo principal"}
          data-column-visibility="visible"
          className="flex flex-[1_1_0%] min-h-0 min-w-0 flex-col overflow-hidden"
        >
          <div
            aria-hidden="true"
            className="h-[var(--app-header-height)] shrink-0"
          />
          <div className="flex flex-1 min-h-0 min-w-0 overflow-hidden">
            {center}
          </div>
        </main>
        {rightVisibility !== "hidden" && (
          <motion.aside
            animate={
              columns.right.label === "Workbench"
                ? {
                    width:
                      rightVisibility === "collapsed"
                        ? WORKBENCH_RAIL_WIDTH
                        : columns.right.width,
                  }
                : undefined
            }
            transition={workbenchTransition}
            aria-label={columns.right.label}
            data-column-visibility={rightVisibility}
            className="flex shrink-0 min-h-0 min-w-0 overflow-hidden"
            style={
              rightVisibility === "collapsed"
                ? { width: WORKBENCH_RAIL_WIDTH }
                : columnStyle(columns.right)
            }
          >
            {rightVisibility === "collapsed" ? (
              <div className="flex h-full w-full items-start justify-center pt-3">
                <RailToggleButton
                  side={direction === "rtl" ? "left" : "right"}
                  ariaLabel={
                    columns.right.expandLabel ??
                    `Expandir ${columns.right.label}`
                  }
                  ariaExpanded={false}
                  onClick={columns.right.onExpand}
                />
              </div>
            ) : (
              right
            )}
          </motion.aside>
        )}
      </div>
    </div>
  );
}
