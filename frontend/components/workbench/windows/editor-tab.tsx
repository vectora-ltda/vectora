"use client";

import { useState } from "react";
import { X } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { m } from "@/lib/paraglide/messages";

interface EditorTabProps {
  name: string;
  path: string;
  active: boolean;
  dirty: boolean;
  onActivate: () => void;
  onClose: () => void;
  onSave: () => Promise<boolean>;
  onSaveAs: (path: string) => Promise<boolean>;
}

export function EditorTab({
  name,
  path,
  active,
  dirty,
  onActivate,
  onClose,
  onSave,
  onSaveAs,
}: EditorTabProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [closeDialogOpen, setCloseDialogOpen] = useState(false);
  const [working, setWorking] = useState(false);

  const requestClose = () => {
    if (dirty) setCloseDialogOpen(true);
    else onClose();
  };

  const saveAndClose = async () => {
    setWorking(true);
    const saved = await onSave();
    setWorking(false);
    if (saved) {
      setCloseDialogOpen(false);
      onClose();
    }
  };

  const saveAs = async () => {
    const target = window.prompt("Salvar como", path);
    if (!target?.trim()) return;
    setWorking(true);
    await onSaveAs(target);
    setWorking(false);
  };

  return (
    <>
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger asChild>
          <div
            role="tab"
            aria-selected={active}
            onClick={onActivate}
            onContextMenu={(event) => {
              event.preventDefault();
              setMenuOpen(true);
              onActivate();
            }}
            className={`group flex cursor-pointer items-center gap-1 border-r border-border/40 px-2 py-1.5 text-[11px] shrink-0 ${
              active
                ? "bg-background font-medium text-foreground"
                : "text-muted-foreground hover:bg-muted/40 hover:text-foreground"
            }`}
            title={path}
          >
            {dirty && (
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500"
                title={m.workbench_files_unsaved()}
              />
            )}
            <span className="max-w-[160px] truncate">{name}</span>
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                requestClose();
              }}
              className="ml-1 shrink-0 rounded p-0.5 opacity-0 hover:bg-muted/60 group-hover:opacity-100"
              aria-label={m.window_close()}
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuItem onSelect={() => void onSave()}>
            {m.workbench_files_save()}
            <DropdownMenuShortcut>
              {m.workbench_files_save_shortcut()}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void saveAs()}>
            {m.workbench_files_save_as()}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={requestClose}>
            {m.window_close()}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={closeDialogOpen} onOpenChange={setCloseDialogOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{m.workbench_files_discard_title()}</DialogTitle>
            <DialogDescription>
              {m.workbench_files_discard_desc()}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCloseDialogOpen(false)}>
              {m.workbench_files_cancel()}
            </Button>
            <Button
              variant="destructive"
              disabled={working}
              onClick={() => {
                setCloseDialogOpen(false);
                onClose();
              }}
            >
              {m.workbench_files_discard()}
            </Button>
            <Button disabled={working} onClick={() => void saveAndClose()}>
              {m.workbench_files_save()}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
