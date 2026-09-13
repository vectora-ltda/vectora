"use client";

import { memo } from "react";
import { PanelLeftClose } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { m } from "@/lib/paraglide/messages";

interface SidebarHeaderProps {
  onToggle: () => void;
  compact?: boolean;
}

export const SidebarHeader = memo(function SidebarHeader({
  onToggle,
  compact = false,
}: SidebarHeaderProps) {
  return (
    <div
      className={
        compact
          ? "absolute top-2 right-2 z-10"
          : "h-16 px-2 flex items-center border-b border-border/40"
      }
    >
      <div
        className={compact ? "" : "flex items-center justify-between w-full"}
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={onToggle}
              aria-label={m.sidebar_collapse()}
              className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-muted/40 transition-colors duration-150 rounded-md"
            >
              <PanelLeftClose className="w-4 h-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">{m.sidebar_collapse()}</TooltipContent>
        </Tooltip>

        {!compact && (
          <>
            <span className="text-xs font-medium text-muted-foreground/70 uppercase tracking-widest select-none">
              {m.sidebar_title()}
            </span>
            <div className="w-7" />
          </>
        )}
      </div>
    </div>
  );
});
