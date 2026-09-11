"use client";

import { useState } from "react";
import {
  Database,
  FolderPlus,
  Paperclip,
  Camera,
  Plus,
  Share2,
  Slash,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from "@/components/ui/popover";
import { useEnvironmentDialogStore } from "@/lib/stores/environment-dialog-store";
import { useWorkspacesStore } from "@/lib/stores/workspaces-store";
import { WorkspaceTrustDialog } from "@/components/sidebar/workspace-trust-dialog";
import { m } from "@/lib/paraglide/messages";

interface PlusMenuProps {
  disabled?: boolean;
  onAddFiles: (e: React.MouseEvent) => void;
  /** Insere "/" no input para disparar o autocomplete de comandos (opcional). */
  onSlashCommands?: () => void;
  onCaptureScreenshot?: () => void;
}

export function PlusMenu({
  disabled,
  onAddFiles,
  onSlashCommands,
  onCaptureScreenshot,
}: PlusMenuProps) {
  const openEnvironment = useEnvironmentDialogStore((s) => s.openAt);
  const activeCwd = useWorkspacesStore((s) => s.getActive()?.cwd);
  const [open, setOpen] = useState(false);
  const [trustOpen, setTrustOpen] = useState(false);
  const [ingestOpen, setIngestOpen] = useState(false);

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                disabled={disabled}
                className="h-7 w-7 p-0 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 border-0 flex-shrink-0 transition-colors duration-150"
                type="button"
                aria-label={m.tooltip_chat_add_files()}
                aria-expanded={open}
                data-testid="plus-menu-trigger"
              >
                <Plus className="w-4 h-4" />
              </Button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent side="top">
            {m.tooltip_chat_add_files()}
          </TooltipContent>
        </Tooltip>

        <PopoverContent
          side="top"
          align="start"
          sideOffset={8}
          className="w-60 p-1 bg-background"
        >
          <button
            className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-foreground/80 hover:text-foreground hover:bg-accent transition-colors text-left rounded-sm"
            onClick={(e) => {
              setOpen(false);
              onAddFiles(e);
            }}
          >
            <Paperclip className="w-4 h-4 shrink-0 text-muted-foreground" />
            {m.plus_add_files()}
          </button>

          {onCaptureScreenshot && (
            <button
              data-testid="plus-menu-capture-screenshot"
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-foreground/80 hover:text-foreground hover:bg-accent transition-colors text-left rounded-sm"
              onClick={() => {
                setOpen(false);
                onCaptureScreenshot();
              }}
            >
              <Camera className="w-4 h-4 shrink-0 text-muted-foreground" />
              {m.plus_capture_screenshot()}
            </button>
          )}

          <button
            data-testid="plus-menu-add-folder"
            className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-foreground/80 hover:text-foreground hover:bg-accent transition-colors text-left rounded-sm"
            onClick={() => {
              setOpen(false);
              setTrustOpen(true);
            }}
          >
            <FolderPlus className="w-4 h-4 shrink-0 text-muted-foreground" />
            {m.plus_add_folder()}
          </button>

          <button
            className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-foreground/80 hover:text-foreground hover:bg-accent transition-colors text-left rounded-sm"
            onClick={() => {
              setOpen(false);
              setIngestOpen(true);
            }}
          >
            <Database className="w-4 h-4 shrink-0 text-muted-foreground" />
            {m.plus_ingest_folder()}
          </button>

          {onSlashCommands && (
            <button
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-foreground/80 hover:text-foreground hover:bg-accent transition-colors text-left rounded-sm"
              onClick={() => {
                setOpen(false);
                onSlashCommands();
              }}
            >
              <Slash className="w-4 h-4 shrink-0 text-muted-foreground" />
              {m.plus_slash_commands()}
            </button>
          )}

          <div className="border-t border-border/60 my-1" />

          <button
            data-testid="plus-menu-connectors"
            className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-foreground/80 hover:text-foreground hover:bg-accent transition-colors text-left rounded-sm"
            onClick={() => {
              setOpen(false);
              openEnvironment("integracoes");
            }}
          >
            <Share2 className="w-4 h-4 shrink-0 text-muted-foreground" />
            {m.plus_connectors()}
          </button>
        </PopoverContent>
      </Popover>

      <WorkspaceTrustDialog open={trustOpen} onOpenChange={setTrustOpen} />
      <WorkspaceTrustDialog
        open={ingestOpen}
        onOpenChange={setIngestOpen}
        mode="ingest"
        initialPath={activeCwd}
      />
    </>
  );
}
