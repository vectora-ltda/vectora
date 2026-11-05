"use client";

/**
 * ConfirmDialog — substitui window.confirm por um Radix Dialog acessível.
 *
 * Focus trap automático (Radix), aria-labelledby/describedby, role="alertdialog".
 * Uso via prop `open`/`onConfirm`/`onCancel` ou via hook useConfirm.
 *
 * @example
 *   const [pending, setPending] = useState<string | null>(null);
 *   <ConfirmDialog
 *     open={pending !== null}
 *     title="Deletar arquivo?"
 *     description={`"${pending}" será removido permanentemente.`}
 *     confirmLabel="Deletar"
 *     variant="destructive"
 *     onConfirm={() => { doDelete(pending!); setPending(null); }}
 *     onCancel={() => setPending(null)}
 *   />
 */

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** "destructive" torna o botão de confirmar vermelho. Default "default". */
  variant?: "default" | "destructive";
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Confirmar",
  cancelLabel = "Cancelar",
  variant = "default",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onCancel()}>
      <DialogContent role="alertdialog" aria-modal="true" className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button
            variant={variant === "destructive" ? "destructive" : "default"}
            onClick={() => void onConfirm()}
            autoFocus
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
