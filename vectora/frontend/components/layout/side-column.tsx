import type { ReactNode } from "react";
import { motion, type Transition } from "motion/react";
import { RailToggleButton } from "@/components/layout/rail-toggle-button";
import {
  WORKBENCH_RAIL_WIDTH,
  WORKBENCH_WIDTH_TRANSITION,
} from "@/lib/layout/workbench-geometry";
import {
  COLLAPSED_RAIL_WIDTH,
  SIDE_COLUMN_MIN_WIDTH,
} from "@/lib/layout/panel-geometry";
import type { ShellColumnState } from "@/components/layout/three-column-shell";
import { m } from "@/lib/paraglide/messages";

interface SideColumnProps {
  side: "left" | "right";
  column: ShellColumnState;
  content: ReactNode | null;
  transition?: Transition;
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

/** Shared left/right sidebar frame. Only the rendered panel content differs. */
export function SideColumn({
  side,
  column,
  content,
  transition = WORKBENCH_WIDTH_TRANSITION,
}: SideColumnProps) {
  const visibility = column.visibility ?? "visible";
  const collapsed = visibility === "collapsed";
  const isWorkbench = column.label === "Workbench";

  if (visibility === "hidden") return null;

  const width = collapsed
    ? isWorkbench
      ? WORKBENCH_RAIL_WIDTH
      : COLLAPSED_RAIL_WIDTH
    : undefined;
  // A width paired with the same minWidth is an explicit fixed-size contract.
  // It is used by rails that must be narrower than the normal open-column
  // floor. Other columns retain the 240px usability floor, including callers
  // that only provide a preferred width.
  const hasFixedNarrowWidth =
    column.width != null && column.minWidth === column.width;

  return (
    <motion.aside
      animate={
        column.width != null
          ? { width: collapsed ? width : column.width }
          : isWorkbench
            ? {
                width: collapsed ? WORKBENCH_RAIL_WIDTH : column.width,
              }
            : undefined
      }
      transition={transition}
      aria-label={column.label}
      data-column-visibility={visibility}
      className={`flex h-full shrink-0 min-h-0 min-w-0 overflow-hidden ${collapsed ? "bg-sidebar" : ""} ${column.label === "Chat" ? (collapsed ? "min-w-12" : "min-w-60") : ""}`}
      style={
        collapsed
          ? { width }
          : {
              ...columnStyle(column),
              minWidth: hasFixedNarrowWidth
                ? column.minWidth
                : Math.max(SIDE_COLUMN_MIN_WIDTH, column.minWidth ?? 0),
            }
      }
    >
      <div className="relative flex h-full min-h-0 w-full flex-col bg-sidebar">
        <div
          className={`flex h-[var(--app-header-height)] min-h-[var(--app-header-height)] shrink-0 items-center justify-center border-b border-border/40 bg-sidebar ${collapsed ? "" : "hidden"}`}
        >
          <RailToggleButton
            side={side}
            ariaLabel={
              column.expandLabel ??
              m.layout_expand_column({ column: column.label })
            }
            ariaExpanded={false}
            onClick={column.onExpand ?? (() => undefined)}
          />
        </div>
        <div
          hidden={collapsed}
          aria-hidden={collapsed}
          className={
            collapsed
              ? "invisible pointer-events-none absolute inset-x-0 bottom-0 top-[var(--app-header-height)] hidden overflow-hidden"
              : "flex min-h-0 min-w-0 flex-1 overflow-hidden"
          }
        >
          {content}
        </div>
        {!collapsed && column.resize && (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label={column.resize.ariaLabel}
            aria-valuemin={column.resize.min}
            aria-valuemax={column.resize.max}
            aria-valuenow={column.resize.value}
            tabIndex={0}
            onKeyDown={column.resize.onKeyDown}
            onPointerDown={column.resize.onPointerDown}
            onPointerMove={column.resize.onPointerMove}
            onPointerUp={column.resize.onPointerUp}
            onPointerCancel={column.resize.onPointerCancel}
            className={`absolute top-0 z-[60] h-full w-1 cursor-col-resize bg-transparent hover:bg-primary/30 transition-colors ${side === "left" ? "right-0" : "left-0"}`}
          />
        )}
      </div>
    </motion.aside>
  );
}
