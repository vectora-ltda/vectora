"use client";

/** Modal de worktrees — substitui a antiga aba Worktrees (abre via toolbar). */

import { Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiCreateWorktree, fetchWorktrees, type WorktreeEntry } from "./api";
import { m } from "@/lib/paraglide/messages";

export function WorktreesModal({
  workspaceId,
  open,
  onOpenChange,
}: {
  workspaceId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [entries, setEntries] = useState<WorktreeEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [name, setName] = useState("");
  const [branch, setBranch] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const r = await fetchWorktrees(workspaceId);
    setLoading(false);
    setEntries(r);
  }, [workspaceId]);

  useEffect(() => {
    // Sincroniza com o backend git (rede) ao abrir o modal, não estado derivado.
    // oxlint-disable-next-line react/set-state-in-effect
    if (open) void load();
  }, [open, load]);

  const handleCreate = useCallback(async () => {
    if (!name.trim()) return;
    await apiCreateWorktree(
      workspaceId,
      name.trim(),
      branch.trim() || undefined,
    );
    setName("");
    setBranch("");
    void load();
  }, [workspaceId, name, branch, load]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{m.workbench_git_worktrees_title()}</DialogTitle>
        </DialogHeader>
        <div className="flex items-center gap-1.5">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={m.workbench_diff_worktree_name_placeholder()}
            className="flex-1 text-xs bg-background border border-border/60 rounded px-1.5 py-1 outline-none focus:border-primary min-w-0"
          />
          <input
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            placeholder={m.workbench_diff_worktree_branch_placeholder()}
            className="flex-1 text-xs font-mono bg-background border border-border/60 rounded px-1.5 py-1 outline-none focus:border-primary min-w-0"
          />
          <button
            onClick={() => void handleCreate()}
            className="text-[10px] px-2 py-1 rounded bg-primary/10 text-primary hover:bg-primary/20 shrink-0"
          >
            {m.workbench_diff_worktree_create()}
          </button>
        </div>
        <div className="max-h-72 overflow-y-auto -mx-1">
          {loading ? (
            <div className="flex justify-center py-6">
              <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
            </div>
          ) : entries.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-6">
              {m.workbench_diff_worktree_empty()}
            </p>
          ) : (
            entries.map((w, i) => (
              <div key={i} className="px-2 py-1.5 rounded hover:bg-muted/30">
                <p
                  className="text-xs font-mono text-foreground truncate"
                  title={w.path}
                >
                  {w.path.split(/[\\/]/).pop()}
                </p>
                <p className="text-[10px] text-muted-foreground truncate">
                  {w.path}
                </p>
                {w.branch && (
                  <p className="text-[10px] text-primary font-mono mt-0.5">
                    {w.branch}
                  </p>
                )}
              </div>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
