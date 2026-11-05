"use client";

/** Modal de stashes — substitui a antiga aba Stash (abre via toolbar). */

import { Archive, Loader2, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiStash, type StashEntry } from "./api";
import { m } from "@/lib/paraglide/messages";

export function StashModal({
  workspaceId,
  open,
  onOpenChange,
  onChanged,
}: {
  workspaceId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged: () => void;
}) {
  const [entries, setEntries] = useState<StashEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [name, setName] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const r = await apiStash(workspaceId, "list");
    setLoading(false);
    setEntries(r.entries);
  }, [workspaceId]);

  useEffect(() => {
    // Sincroniza com o backend git (rede) ao abrir o modal, não estado derivado.
    // oxlint-disable-next-line react/set-state-in-effect
    if (open) void load();
  }, [open, load]);

  const act = useCallback(
    async (
      action: "push" | "pop" | "apply" | "drop",
      opts: { name?: string; index?: number } = {},
    ) => {
      await apiStash(workspaceId, action, opts);
      onChanged();
      void load();
    },
    [workspaceId, onChanged, load],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{m.workbench_git_stash_title()}</DialogTitle>
        </DialogHeader>
        <div className="flex items-center gap-1.5">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={m.workbench_diff_stash_name_placeholder()}
            className="flex-1 text-xs bg-background border border-border/60 rounded px-1.5 py-1 outline-none focus:border-primary min-w-0"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                void act("push", { name: name || undefined });
                setName("");
              }
            }}
          />
          <button
            onClick={() => {
              void act("push", { name: name || undefined });
              setName("");
            }}
            title={m.workbench_diff_stash_push()}
            className="p-1 rounded hover:bg-muted/40 text-muted-foreground hover:text-foreground"
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>
        <div className="max-h-72 overflow-y-auto -mx-1">
          {loading ? (
            <div className="flex justify-center py-6">
              <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
            </div>
          ) : entries.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-6">
              {m.workbench_diff_stash_empty()}
            </p>
          ) : (
            entries.map((e) => (
              <div
                key={e.index}
                className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-muted/30 group"
              >
                <Archive className="w-3 h-3 text-muted-foreground shrink-0" />
                <span className="flex-1 truncate text-xs">{e.label}</span>
                <button
                  onClick={() => void act("pop")}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary hover:bg-primary/20"
                >
                  {m.workbench_diff_stash_pop()}
                </button>
                <button
                  onClick={() => void act("drop", { index: e.index })}
                  title={m.workbench_diff_stash_drop()}
                  className="p-0.5 opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-opacity"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
