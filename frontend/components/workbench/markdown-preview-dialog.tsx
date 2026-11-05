"use client";

/**
 * MarkdownPreviewDialog — visualiza arquivos .md em modal, render GitHub.
 */

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { MarkdownView } from "@/components/workbench/markdown-view";
import { m } from "@/lib/paraglide/messages";
interface MarkdownPreviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filePath?: string;
  content?: string;
}

export function MarkdownPreviewDialog({
  open,
  onOpenChange,
  filePath,
  content: initialContent,
}: MarkdownPreviewDialogProps) {
  const [content, setContent] = useState<string | null>(initialContent ?? null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!open || !filePath || initialContent) return;

    // Busca o conteúdo do arquivo via rede (sistema externo), não estado derivado.
    // oxlint-disable-next-line react/set-state-in-effect
    setIsLoading(true);
    fetch(filePath)
      .then((res) => res.text())
      .then(setContent)
      .catch(() => setContent(null))
      .finally(() => setIsLoading(false));
  }, [open, filePath, initialContent]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[80vh] overflow-hidden">
        <DialogHeader>
          <DialogTitle className="truncate">
            {filePath || m.workbench_preview_md_title()}
          </DialogTitle>
        </DialogHeader>

        <div className="flex-1 overflow-auto custom-scrollbar">
          {isLoading ? (
            <div className="flex items-center justify-center h-32 text-muted-foreground">
              {m.workbench_preview_md_loading()}
            </div>
          ) : content ? (
            <MarkdownView content={content} />
          ) : (
            <div className="p-4 text-muted-foreground">
              {m.workbench_preview_md_empty()}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
