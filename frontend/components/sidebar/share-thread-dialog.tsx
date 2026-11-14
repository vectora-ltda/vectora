"use client";

import { useEffect, useState } from "react";
import { Check, Copy, Link2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { createShare, listShares, revokeShare } from "@/lib/api/vectora-client";
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

/** Lista, cria, copia e revoga os links de leitura de uma conversa. */
export function ShareThreadDialog({
  threadId,
  open,
  onOpenChange,
}: ShareThreadDialogProps) {
  const [ttlHours, setTtlHours] = useState<number>(72);
  const [shares, setShares] = useState<CreatedShare[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [copiedToken, setCopiedToken] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    listShares(threadId)
      .then((result) => {
        if (active) setShares(result.shares);
      })
      .catch(() => {
        if (active) {
          setShares([]);
          setError(true);
        }
      });
    return () => {
      active = false;
    };
  }, [threadId]);

  const handleCreate = async () => {
    setBusy(true);
    setError(false);
    try {
      const created = await createShare(threadId, ttlHours);
      setShares((current) => [created, ...(current ?? [])]);
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  const handleCopy = async (share: CreatedShare) => {
    try {
      await navigator.clipboard.writeText(share.url);
      setCopiedToken(share.token);
    } catch {
      setError(true);
    }
  };

  const handleRevoke = async (share: CreatedShare) => {
    setBusy(true);
    setError(false);
    try {
      await revokeShare(share.token);
      setShares((current) =>
        (current ?? []).filter((item) => item.token !== share.token),
      );
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{m.share_dialog_title()}</DialogTitle>
          <DialogDescription>{m.share_dialog_description()}</DialogDescription>
        </DialogHeader>

        <section
          aria-label={m.share_dialog_active_title()}
          className="grid gap-2"
        >
          <h3 className="text-sm font-medium">
            {m.share_dialog_active_title()}
          </h3>
          {shares === null ? (
            <p className="text-sm text-muted-foreground">
              {m.share_dialog_loading()}
            </p>
          ) : shares.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {m.share_dialog_none()}
            </p>
          ) : (
            <ul className="grid gap-2">
              {shares.map((share) => (
                <li
                  key={share.token}
                  className="grid gap-2 rounded-md border border-border p-2"
                >
                  <input
                    readOnly
                    aria-label={m.share_dialog_link_label()}
                    value={share.url}
                    onFocus={(event) => event.currentTarget.select()}
                    className="w-full rounded-md border border-border bg-background px-2 py-1 text-xs"
                  />
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs text-muted-foreground">
                      {m.share_dialog_expires({
                        date: new Date(share.expires_at).toLocaleString(),
                      })}
                    </span>
                    <span className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => void handleCopy(share)}
                        className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs hover:bg-muted"
                      >
                        {copiedToken === share.token ? (
                          <Check className="h-3 w-3" />
                        ) : (
                          <Copy className="h-3 w-3" />
                        )}
                        {copiedToken === share.token
                          ? m.share_dialog_copied()
                          : m.share_dialog_copy()}
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleRevoke(share)}
                        disabled={busy}
                        className="rounded-md border border-border px-2 py-1 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-60"
                      >
                        {m.share_dialog_revoke()}
                      </button>
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="grid gap-2">
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
          <button
            type="button"
            onClick={() => void handleCreate()}
            disabled={busy}
            className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-60"
          >
            <Link2 className="h-4 w-4" />
            {m.share_dialog_create()}
          </button>
        </section>

        {error && (
          <p role="alert" className="text-sm text-destructive">
            {m.share_dialog_error()}
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
