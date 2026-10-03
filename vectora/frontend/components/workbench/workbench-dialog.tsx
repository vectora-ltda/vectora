"use client";

import type { ReactNode, RefObject } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface WorkbenchDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  bodyRef?: RefObject<HTMLDivElement | null>;
  testId?: string;
  children: ReactNode;
  bodyMode?: "scroll" | "fill";
}

/** Host modal responsivo compartilhado pelas configurações das workbenches. */
export function WorkbenchDialog({
  open,
  onOpenChange,
  title,
  description,
  bodyRef,
  testId,
  children,
  bodyMode = "scroll",
}: WorkbenchDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid={testId}
        className="flex h-[min(85vh,52rem)] w-[min(92vw,78rem)] max-w-none flex-col gap-0 overflow-hidden p-0"
      >
        <DialogHeader className="shrink-0 border-b border-border/60 px-4 py-3 pr-12">
          <DialogTitle className="truncate text-sm">{title}</DialogTitle>
          <DialogDescription className={description ? undefined : "sr-only"}>
            {description ?? title}
          </DialogDescription>
        </DialogHeader>
        <div
          ref={bodyRef}
          data-testid="workbench-dialog-body"
          className={`min-h-0 flex-1 overflow-x-hidden ${
            bodyMode === "fill"
              ? "flex flex-col overflow-y-hidden"
              : "overflow-y-auto"
          }`}
        >
          {children}
        </div>
      </DialogContent>
    </Dialog>
  );
}
