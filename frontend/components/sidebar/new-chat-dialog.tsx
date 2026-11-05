"use client";

import { useEffect, useState } from "react";
import {
  Check,
  FolderGit2,
  FolderOpen,
  FolderPlus,
  Loader2,
  Plus,
} from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { WorkspaceTrustDialog } from "./workspace-trust-dialog";
import { m } from "@/lib/paraglide/messages";

interface NewChatDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `workspaceId === null` pede ao backend para criar um workspace dedicado. */
  onConfirm: (workspaceId: string | null) => void;
}

/** Pede ao usuário um workspace para a nova conversa: reusar um existente,
 * adicionar uma nova pasta ou deixar o backend criar um dedicado em
 * `~/Documents/vectora/<thread_id>`. */
export function NewChatDialog({
  open,
  onOpenChange,
  onConfirm,
}: NewChatDialogProps) {
  const workspaces = useWorkspacesStore((s) => s.workspaces);
  const activeId = useWorkspacesStore((s) => s.active_id);
  const status = useWorkspacesStore((s) => s.status);
  const hydrate = useWorkspacesStore((s) => s.hydrate);
  const isLoadingWorkspaces = status === "loading" && workspaces.length === 0;

  const [selected, setSelected] = useState<string | null>(null);
  const [trustOpen, setTrustOpen] = useState(false);

  // Reseta a seleção pro workspace ativo sempre que o diálogo reabre —
  // comparação durante o render (não num effect), como recomendado em
  // https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) setSelected(activeId ?? null);
  }

  useEffect(() => {
    // Rehidrata a lista de workspaces do backend (rede) ao abrir o diálogo.
    // oxlint-disable-next-line react/set-state-in-effect
    if (open) void hydrate();
  }, [open, hydrate]);

  function handleConfirm() {
    onConfirm(selected);
    onOpenChange(false);
  }

  function handleTrustOpenChange(isOpen: boolean) {
    setTrustOpen(isOpen);
    if (!isOpen) {
      // WorkspaceTrustDialog chama store.create() que seta active_id
      const newId = useWorkspacesStore.getState().active_id;
      if (newId) setSelected(newId);
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{m.new_chat_dialog_title()}</DialogTitle>
            <DialogDescription>{m.new_chat_dialog_desc()}</DialogDescription>
          </DialogHeader>

          <ScrollArea className="max-h-72">
            <div className="space-y-1">
              <button
                type="button"
                onClick={() => setSelected(null)}
                className={`w-full flex items-start gap-3 rounded-md border px-3 py-2.5 text-left transition-colors ${
                  selected === null
                    ? "border-border/80 bg-muted/60"
                    : "border-border hover:bg-muted/50"
                }`}
              >
                <FolderPlus className="w-4 h-4 mt-0.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-foreground">
                    {m.new_chat_create_new()}
                  </span>
                  <span className="block text-xs text-muted-foreground mt-0.5">
                    {m.new_chat_create_new_desc()}
                  </span>
                </span>
                {selected === null && (
                  <Check className="w-4 h-4 mt-0.5 shrink-0 text-foreground" />
                )}
              </button>

              {isLoadingWorkspaces && (
                <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
                  <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
                  {m.new_chat_loading_workspaces()}
                </div>
              )}

              {!isLoadingWorkspaces && workspaces.length > 0 && (
                <p className="px-1 pt-2 pb-1 text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  {m.new_chat_existing_label()}
                </p>
              )}

              {workspaces.map((ws) => (
                <button
                  key={ws.id}
                  type="button"
                  onClick={() => setSelected(ws.id)}
                  className={`w-full flex items-start gap-3 rounded-md border px-3 py-2.5 text-left transition-colors ${
                    selected === ws.id
                      ? "border-border/80 bg-muted/60"
                      : "border-border hover:bg-muted/50"
                  }`}
                >
                  {ws.is_git_repo ? (
                    <FolderGit2 className="w-4 h-4 mt-0.5 shrink-0 text-muted-foreground" />
                  ) : (
                    <FolderOpen className="w-4 h-4 mt-0.5 shrink-0 text-muted-foreground" />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium text-foreground truncate">
                      {ws.name}
                    </span>
                    <span className="block text-xs text-muted-foreground truncate mt-0.5">
                      {ws.cwd}
                    </span>
                  </span>
                  {selected === ws.id && (
                    <Check className="w-4 h-4 mt-0.5 shrink-0 text-foreground" />
                  )}
                </button>
              ))}

              <button
                type="button"
                onClick={() => setTrustOpen(true)}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-foreground/80 hover:text-foreground hover:bg-muted/50 rounded-md transition-colors text-left mt-1"
              >
                <Plus className="w-4 h-4 shrink-0 text-muted-foreground" />
                {m.workspace_add_folder()}
              </button>
            </div>
          </ScrollArea>

          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {m.new_chat_cancel()}
            </Button>
            <Button onClick={handleConfirm}>{m.new_chat_confirm()}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <WorkspaceTrustDialog
        open={trustOpen}
        onOpenChange={handleTrustOpenChange}
      />
    </>
  );
}
