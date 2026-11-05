import { AtSign, Pencil, Pin, Trash2 } from "lucide-react";
import { useState } from "react";

import { FileIcon } from "@/components/icons/file-icon";
import {
  useWorkbenchStore,
  type FileEntry,
} from "@/lib/stores/workbench-store";
import { useWindowsStore } from "@/lib/stores/windows-store";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { m } from "@/lib/paraglide/messages";

import { GitBadge } from "./git-badge";
import { FS_DRAG_MIME } from "./files-utils";

/** Linha de um arquivo na árvore: abrir, renomear inline, ações em hover
 * (@ contexto, abrir como janela, renomear, deletar, fixar). */
export function FileItem({
  threadId,
  workspaceId,
  entry,
  depth,
  status,
  onOpenFile,
  onAddToContext,
  onDelete,
  onRename,
}: {
  threadId: string;
  workspaceId: string;
  entry: FileEntry;
  depth: number;
  status?: string;
  onOpenFile: (path: string) => void;
  onAddToContext?: (path: string) => void;
  onDelete: (path: string, name: string, permanent?: boolean) => void;
  onRename?: (oldPath: string, newName: string) => void;
}) {
  const pinned = useWorkbenchStore((s) => s.isPinned(threadId, entry.path));
  const togglePinned = useWorkbenchStore((s) => s.togglePinned);
  const openPath = useWorkbenchStore((s) => s.getFiles(workspaceId).openPath);
  const openDocked = useWindowsStore((s) => s.openDocked);
  const openCanvasDocument = useWindowsStore((s) => s.openCanvasDocument);
  const uiMode = useSettingsStore((s) => s.uiMode);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(entry.name);

  const commitRename = () => {
    const trimmed = renameValue.trim();
    if (trimmed && trimmed !== entry.name && onRename) {
      onRename(entry.path, trimmed);
    }
    setRenaming(false);
  };

  return (
    <div
      tabIndex={0}
      role="treeitem"
      aria-selected={openPath === entry.path}
      draggable={!renaming}
      onDragStart={(e) => {
        e.dataTransfer.setData(FS_DRAG_MIME, entry.path);
        e.dataTransfer.effectAllowed = "move";
      }}
      className="group flex items-center px-2 py-0.5 text-xs hover:bg-muted/50 rounded-sm focus:outline-none focus-visible:ring-1 focus-visible:ring-primary/40"
      style={{ paddingLeft: 8 + depth * 12 }}
      onKeyDown={(e) => {
        if (e.key === "Delete" && !renaming) {
          e.preventDefault();
          onDelete(entry.path, entry.name, e.shiftKey);
        }
      }}
    >
      <span className="w-3" />
      <FileIcon name={entry.name} />
      {renaming ? (
        <input
          autoFocus
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commitRename();
            } else if (e.key === "Escape") {
              setRenaming(false);
              setRenameValue(entry.name);
            }
          }}
          onBlur={commitRename}
          className="flex-1 text-xs bg-background border border-primary/60 rounded px-1.5 py-0.5 outline-none focus:ring-1 focus:ring-primary/40 font-mono ml-1"
          placeholder={m.workbench_files_rename_placeholder()}
        />
      ) : (
        <button
          onClick={() => {
            // Janela flutuante só onde o `WindowLayer` monta (modo Assistente);
            if (uiMode === "assistant") {
              openCanvasDocument({
                id: `file:${workspaceId}:${entry.path}`,
                kind: "file",
                workspaceId,
                threadId,
                title: entry.name,
                path: entry.path,
              });
            } else {
              openDocked(workspaceId, entry.path);
            }
            void fetch(
              `/workspaces/${encodeURIComponent(workspaceId)}/context/active`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ open_file: entry.path }),
              },
            ).catch(() => undefined);
          }}
          onDoubleClick={() => {
            if (onRename) {
              setRenameValue(entry.name);
              setRenaming(true);
            }
          }}
          className="flex-1 text-left truncate text-foreground/80 hover:text-foreground ml-1"
          title={onRename ? m.workbench_files_rename() : undefined}
        >
          {entry.name}
        </button>
      )}
      <GitBadge status={status} />

      {/* Ações em hover */}
      <div className="hidden group-hover:flex items-center gap-0.5">
        {onAddToContext && (
          <button
            onClick={() => onAddToContext(entry.path)}
            className="p-0.5 rounded text-muted-foreground hover:text-foreground"
            aria-label={m.workbench_files_add_context()}
            title={m.workbench_files_add_context()}
          >
            <AtSign className="w-3 h-3" />
          </button>
        )}
        {onRename && !renaming && (
          <button
            onClick={() => {
              setRenameValue(entry.name);
              setRenaming(true);
            }}
            className="p-0.5 rounded text-muted-foreground hover:text-foreground"
            aria-label={m.workbench_files_rename()}
            title={m.workbench_files_rename()}
          >
            <Pencil className="w-3 h-3" />
          </button>
        )}
        <button
          onClick={(e) => onDelete(entry.path, entry.name, e.shiftKey)}
          className="p-0.5 rounded text-muted-foreground hover:text-destructive"
          aria-label={m.workbench_files_delete()}
          title={`${m.workbench_files_delete()} (Shift: permanente)`}
        >
          <Trash2 className="w-3 h-3" />
        </button>
        <button
          onClick={() => togglePinned(threadId, entry.path)}
          className={`p-0.5 rounded ${
            pinned
              ? "text-primary"
              : "text-muted-foreground hover:text-foreground"
          }`}
          aria-label={
            pinned ? m.workbench_files_unpin() : m.workbench_files_pin()
          }
          title={pinned ? m.workbench_files_unpin() : m.workbench_files_pin()}
        >
          <Pin className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
}
