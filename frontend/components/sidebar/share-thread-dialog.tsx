"use client";

import { useState } from "react";
import { Check, Copy, Link2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { createShare, revokeShare } from "@/lib/api/vectora-client";
import type { CreatedShare } from "@/lib/api/vectora-client";
import { m } from "@/lib/paraglide/messages";

/** Validades oferecidas ao criar o link, em horas. */
const TTL_OPTIONS_HOURS = [24, 72, 168, 720] as const;

interface ShareThreadDialogProps {
  threadId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function ttlLabel(hours: number): string {
  if (hours < 72) return m.share_dialog_ttl_day();
  if (hours === 72) return m.share_dialog_ttl_three_days();
  if (hours === 168) return m.share_dialog_ttl_week();
  return m.share_dialog_ttl_month();
}

/** Cria, copia e revoga o link de leitura de uma conversa. */
export function ShareThreadDialog({
  threadId,
  open,
  onOpenChange,
}: ShareThreadDialogProps) {
  const [ttlHours, setTtlHours] = useState<number>(72);
  const [share, setShare] = useState<CreatedShare | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setShare(null);
      setCopied(false);
      setError(false);
    }
    onOpenChange(next);
  };

  const handleCreate = async () => {
    setBusy(true);
    setError(false);
    try {
      setShare(await createShare(threadId, ttlHours));
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  const handleCopy = async () => {
    if (!share) return;
    try {
      await navigator.clipboard.writeText(share.url);
      setCopied(true);
    } catch {
      setError(true);
    }
  };

  const handleRevoke = async () => {
    if (!share) return;
    setBusy(true);
    setError(false);
    try {
      await revokeShare(share.token);
      setShare(null);
      setCopied(false);
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{m.share_dialog_title()}</DialogTitle>
          <DialogDescription>{m.share_dialog_description()}</DialogDescription>
        </DialogHeader>

        {share ? (
          <div className="grid gap-3">
            <label className="grid gap-1 text-sm">
              <span className="text-muted-foreground">
                {m.share_dialog_link_label()}
              </span>
              <input
                readOnly
                value={share.url}
                onFocus={(event) => event.currentTarget.select()}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
            <p className="text-xs text-muted-foreground">
              {m.share_dialog_expires({
                date: new Date(share.expires_at).toLocaleString(),
              })}
            </p>
          </div>
        ) : (
          <label className="grid gap-1 text-sm">
            <span className="text-muted-foreground">
              {m.share_dialog_ttl_label()}
            </span>
            <select
              value={ttlHours}
              onChange={(event) => setTtlHours(Number(event.target.value))}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            >
              {TTL_OPTIONS_HOURS.map((hours) => (
                <option key={hours} value={hours}>
                  {ttlLabel(hours)}
                </option>
              ))}
            </select>
          </label>
        )}

        {error && (
          <p role="alert" className="text-sm text-destructive">
            {m.share_dialog_error()}
          </p>
        )}

        <DialogFooter>
          {share ? (
            <>
              <button
                type="button"
                onClick={() => void handleRevoke()}
                disabled={busy}
                className="rounded-md border border-border px-3 py-2 text-sm text-destructive hover:bg-destructive/10 disabled:opacity-60"
              >
                {m.share_dialog_revoke()}
              </button>
              <button
                type="button"
                onClick={() => void handleCopy()}
                className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground"
              >
                {copied ? (
                  <Check className="h-4 w-4" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
                {copied ? m.share_dialog_copied() : m.share_dialog_copy()}
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => void handleCreate()}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-60"
            >
              <Link2 className="h-4 w-4" />
              {m.share_dialog_create()}
            </button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
