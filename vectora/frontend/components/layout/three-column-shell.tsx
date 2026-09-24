import type { ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import { RailToggleButton } from "@/components/layout/rail-toggle-button";
import { MOTION_INSTANT } from "@/lib/motion/transitions";
import {
  WORKBENCH_RAIL_WIDTH,
  WORKBENCH_WIDTH_TRANSITION,
} from "@/lib/layout/workbench-geometry";
import {
  COLLAPSED_RAIL_WIDTH,
  SIDE_COLUMN_MIN_WIDTH,
} from "@/lib/layout/panel-geometry";

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

function expandedColumnStyle(column: ShellColumnState): React.CSSProperties {
  return {
    ...columnStyle(column),
    minWidth: Math.max(SIDE_COLUMN_MIN_WIDTH, column.minWidth ?? 0),
  };
}

function ShellColumnContent({
  side,
  ariaLabel,
  onExpand,
  content,
  collapsed,
}: {
  side: "left" | "right";
  ariaLabel: string;
  onExpand: () => void;
  content: ReactNode | null;
  collapsed: boolean;
}) {
  return (
    <div className="relative flex h-full min-h-0 w-full flex-col bg-sidebar">
      <div
        className={`flex h-[var(--app-header-height)] min-h-[var(--app-header-height)] shrink-0 items-center justify-center border-b border-border/40 bg-sidebar ${collapsed ? "" : "hidden"}`}
      >
        <RailToggleButton
          side={side}
          ariaLabel={ariaLabel}
          ariaExpanded={false}
          onClick={onExpand}
        />
      </div>
      <div
        aria-hidden="true"
        className={
          collapsed
            ? "invisible pointer-events-none absolute inset-x-0 bottom-0 top-[var(--app-header-height)] overflow-hidden"
            : "flex min-h-0 min-w-0 flex-1 overflow-hidden"
        }
      >
        {content}
      </div>
    </div>
  );
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
            className={`flex shrink-0 min-h-0 min-w-0 overflow-hidden ${
              leftVisibility === "collapsed" ? "bg-sidebar" : ""
            }`}
            style={
              leftVisibility === "collapsed"
                ? { width: WORKBENCH_RAIL_WIDTH }
                : expandedColumnStyle(columns.left)
            }
          >
            <ShellColumnContent
              side={direction === "rtl" ? "right" : "left"}
              ariaLabel={
                columns.left.expandLabel ?? `Expandir ${columns.left.label}`
              }
              onExpand={columns.left.onExpand ?? (() => undefined)}
              content={left}
              collapsed={leftVisibility === "collapsed"}
            />
          </motion.aside>
        )}
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
            className={`flex shrink-0 min-h-0 min-w-0 overflow-hidden ${
              rightVisibility === "collapsed" ? "bg-sidebar" : ""
            } ${
              columns.right.label === "Chat"
                ? rightVisibility === "collapsed"
                  ? "min-w-12"
                  : "min-w-60"
                : ""
            }`}
            style={
              rightVisibility === "collapsed"
                ? { width: COLLAPSED_RAIL_WIDTH }
                : expandedColumnStyle(columns.right)
            }
          >
            <ShellColumnContent
              side={direction === "rtl" ? "left" : "right"}
              ariaLabel={
                columns.right.expandLabel ?? `Expandir ${columns.right.label}`
              }
              onExpand={columns.right.onExpand ?? (() => undefined)}
              content={right}
              collapsed={rightVisibility === "collapsed"}
            />
          </motion.aside>
        )}
      </div>
    </div>
  );
}
