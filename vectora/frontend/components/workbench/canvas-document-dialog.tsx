"use client";

import type { ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface CanvasDocumentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  titleClassName?: string;
  children: ReactNode;
}

/** Modal único para documentos abertos a partir do canvas compartilhado. */
export function CanvasDocumentDialog({
  open,
  onOpenChange,
  title,
  titleClassName,
  children,
}: CanvasDocumentDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[min(85vh,52rem)] w-[min(92vw,78rem)] max-w-none flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 border-b border-border/60 px-4 py-3">
          <DialogTitle className={titleClassName ?? "truncate text-sm"}>
            {title}
          </DialogTitle>
        </DialogHeader>
        <div className="min-h-0 flex-1">{children}</div>
      </DialogContent>
    </Dialog>
  );
}
