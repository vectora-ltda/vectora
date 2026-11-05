import {
  AtSign,
  ChevronRight,
  FolderClosed,
  Loader2,
  Pencil,
  Trash2,
} from "lucide-react";
import { useCallback, useMemo, useState, type DragEvent } from "react";

import { useWorkbenchSWR } from "@/lib/hooks/workbench/use-swr";
import { useDelayedLoading } from "@/lib/hooks/use-delayed-loading";
import { useToastStore } from "@/lib/stores/toast-store";
import {
  WORKBENCH_STALE_MS,
  useWorkbenchStore,
} from "@/lib/stores/workbench-store";
import { FileTreeSkeleton } from "@/components/workbench/tabs/file-tree-skeleton";
import { m } from "@/lib/paraglide/messages";

import { fetchTree, apiFsMove } from "./files-api";
import { norm, FS_DRAG_MIME } from "./files-utils";
import { FileItem } from "./file-item";
import { InlineCreateInput } from "./inline-create-input";

/** Estado de criação inline na árvore (qual tipo, em qual diretório). */
export interface CreatingState {
  type: "file" | "dir";
  parentDir: string; // path relativo do diretório onde criar
}

interface DirNodeProps {
  threadId: string;
  workspaceId: string;
  path: string;
  name: string;
  depth: number;
  filter: string;
  statusByPath: Map<string, string>;
  onOpenFile: (path: string) => void;
  onAddToContext?: (path: string) => void;
  onDelete: (path: string, name: string, permanent?: boolean) => void;
  creating: CreatingState | null;
  onInlineCreate: (name: string) => void;
  onCancelCreate: () => void;
  onRequestCreate: (type: "file" | "dir", parentDir: string) => void;
  /** rename/move: callback para renomear esta pasta ou arquivo filho */
  onRename?: (oldPath: string, newName: string) => void;
  /** drag-and-drop: solto um item (arquivo ou pasta) sobre esta pasta —
   * `sourcePath` é o item arrastado, `targetDir` é sempre `path` desta
   * DirNode (injetado aqui, não pelo chamador). */
  onMoveInto?: (sourcePath: string, targetDir: string) => void;
}

/** Nó de diretório expansível na árvore, recursivo. Carrega entradas via SWR,
 * propaga rename/move por subárvore e hospeda o input de criação inline. */
export function DirNode({
  threadId,
  workspaceId,
  path,
  name,
  depth,
  filter,
  statusByPath,
  onOpenFile,
  onAddToContext,
  onDelete,
  creating,
  onInlineCreate,
  onCancelCreate,
  onRequestCreate,
  onRename,
  onMoveInto,
}: DirNodeProps) {
  const [renamingDir, setRenamingDir] = useState(false);
  const [renameDirValue, setRenameDirValue] = useState(name);
  const [dragOver, setDragOver] = useState(false);

  const handleDragOver = useCallback((e: DragEvent) => {
    if (!e.dataTransfer.types.includes(FS_DRAG_MIME)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => setDragOver(false), []);

  const handleDrop = useCallback(
    (e: DragEvent) => {
      setDragOver(false);
      const sourcePath = e.dataTransfer.getData(FS_DRAG_MIME);
      if (!sourcePath || !onMoveInto) return;
      e.preventDefault();
      e.stopPropagation();
      onMoveInto(sourcePath, path);
    },
    [onMoveInto, path],
  );

  const commitDirRename = () => {
    const trimmed = renameDirValue.trim();
    if (trimmed && trimmed !== name && onRename) {
      onRename(path, trimmed);
    }
    setRenamingDir(false);
  };
  const expanded = useWorkbenchStore((s) =>
    depth === 0 ? true : s.getFiles(workspaceId).expandedDirs.includes(path),
  );
  const entries = useWorkbenchStore(
    (s) => s.getFiles(workspaceId).entriesByDir[path],
  );
  const fetchedAt = useWorkbenchStore(
    (s) => s.getFiles(workspaceId).fetchedAt[path] ?? 0,
  );
  const toggleExpanded = useWorkbenchStore((s) => s.toggleExpanded);
  const setFilesEntries = useWorkbenchStore((s) => s.setFilesEntries);

  const revalidate = useCallback(async () => {
    const data = await fetchTree(workspaceId, path);
    if (data) setFilesEntries(workspaceId, path, data);
  }, [workspaceId, path, setFilesEntries]);

  // rename/move: chamado por FileItem / DirNode filho ao confirmar rename.
  const handleChildRename = useCallback(
    async (oldPath: string, newName: string) => {
      const parentDir = oldPath.includes("/")
        ? oldPath.split("/").slice(0, -1).join("/")
        : "";
      const toPath = parentDir ? `${parentDir}/${newName}` : newName;
      const result = await apiFsMove(workspaceId, oldPath, toPath);
      if (!result.ok) {
        const msg =
          result.message === "Já existe um arquivo ou pasta com esse nome."
            ? m.workbench_files_rename_exists()
            : m.workbench_files_rename_error();
        useToastStore.getState().error(msg);
      }
      await revalidate();
    },
    [workspaceId, revalidate],
  );

  useWorkbenchSWR({
    key: `files:${workspaceId}:${path}`,
    hasCache: Array.isArray(entries),
    isStale: () => Date.now() - fetchedAt > WORKBENCH_STALE_MS,
    revalidate,
    skip: !expanded,
  });

  // skeleton só na raiz (primeira carga da árvore); subpastas usam o spinner
  // inline de "…" para não competir visualmente com o nó pai.
  const showRootSkeleton = useDelayedLoading(
    depth === 0 && expanded && !entries,
  );

  const visible = useMemo(() => {
    if (!filter || !entries) return entries ?? [];
    const f = filter.toLowerCase();
    return entries.filter((e) => e.name.toLowerCase().includes(f));
  }, [entries, filter]);

  // Verifica se o inline create input deve aparecer neste diretório
  const showCreateHere =
    creating !== null && creating.parentDir === path && expanded;

  return (
    <div
      onDragOver={depth === 0 ? handleDragOver : undefined}
      onDragLeave={depth === 0 ? handleDragLeave : undefined}
      onDrop={depth === 0 ? handleDrop : undefined}
      className={
        depth === 0 && dragOver ? "bg-primary/5 ring-1 ring-primary/30" : ""
      }
    >
      {depth > 0 && (
        <div
          tabIndex={0}
          role="treeitem"
          aria-expanded={expanded}
          draggable={!renamingDir}
          onDragStart={(e) => {
            e.dataTransfer.setData(FS_DRAG_MIME, path);
            e.dataTransfer.effectAllowed = "move";
          }}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`group flex items-center px-2 py-0.5 text-xs text-foreground/80 hover:bg-muted/50 rounded-sm focus:outline-none focus-visible:ring-1 focus-visible:ring-primary/40 ${
            dragOver ? "bg-primary/10 ring-1 ring-primary/40" : ""
          }`}
          onKeyDown={(e) => {
            if (e.key === "Delete") {
              e.preventDefault();
              onDelete(path, name, e.shiftKey);
            }
          }}
        >
          {renamingDir ? (
            <input
              autoFocus
              value={renameDirValue}
              onChange={(e) => setRenameDirValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  commitDirRename();
                } else if (e.key === "Escape") {
                  setRenamingDir(false);
                  setRenameDirValue(name);
                }
              }}
              onBlur={commitDirRename}
              className="flex-1 text-xs bg-background border border-primary/60 rounded px-1.5 py-0.5 outline-none focus:ring-1 focus:ring-primary/40 font-mono"
              style={{ paddingLeft: (depth - 1) * 12 + 4 }}
              placeholder={m.workbench_files_rename_placeholder()}
            />
          ) : (
            <button
              onClick={() => toggleExpanded(workspaceId, path)}
              onDoubleClick={() => {
                if (onRename && depth > 0) {
                  setRenameDirValue(name);
                  setRenamingDir(true);
                }
              }}
              className="flex items-center gap-1 flex-1 min-w-0"
              style={{ paddingLeft: (depth - 1) * 12 }}
            >
              <ChevronRight
                className={`w-3 h-3 shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
              />
              <FolderClosed className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate">{name}</span>
            </button>
          )}

          {/* Ações em hover na pasta */}
          <div className="hidden group-hover:flex items-center gap-0.5 shrink-0">
            {onAddToContext && (
              <button
                onClick={() => onAddToContext(path)}
                className="p-0.5 rounded text-muted-foreground hover:text-foreground"
                title={m.workbench_files_add_context()}
              >
                <AtSign className="w-3 h-3" />
              </button>
            )}
            {onRename && !renamingDir && (
              <button
                onClick={() => {
                  setRenameDirValue(name);
                  setRenamingDir(true);
                }}
                className="p-0.5 rounded text-muted-foreground hover:text-foreground"
                title={m.workbench_files_rename()}
              >
                <Pencil className="w-3 h-3" />
              </button>
            )}
            <button
              onClick={(e) => onDelete(path, name, e.shiftKey)}
              className="p-0.5 rounded text-muted-foreground hover:text-destructive"
              title={`${m.workbench_files_delete()} (Shift: permanente)`}
            >
              <Trash2 className="w-3 h-3" />
            </button>
          </div>
        </div>
      )}

      {expanded && (
        <div>
          {!entries && depth === 0 ? (
            showRootSkeleton && <FileTreeSkeleton />
          ) : !entries ? (
            <div
              className="flex items-center gap-2 text-xs text-muted-foreground py-1"
              style={{ paddingLeft: 8 + depth * 12 }}
            >
              <Loader2 className="w-3 h-3 animate-spin" />
              <span>…</span>
            </div>
          ) : null}

          {/* Input de criação inline */}
          {showCreateHere && (
            <InlineCreateInput
              placeholder={
                creating!.type === "file"
                  ? m.workbench_files_creating_file()
                  : m.workbench_files_creating_folder()
              }
              onConfirm={onInlineCreate}
              onCancel={onCancelCreate}
              depth={depth}
            />
          )}

          {entries &&
            visible.map((entry) =>
              entry.kind === "dir" ? (
                <DirNode
                  key={entry.path}
                  threadId={threadId}
                  workspaceId={workspaceId}
                  path={entry.path}
                  name={entry.name}
                  depth={depth + 1}
                  filter={filter}
                  statusByPath={statusByPath}
                  onOpenFile={onOpenFile}
                  onAddToContext={onAddToContext}
                  onDelete={onDelete}
                  creating={creating}
                  onInlineCreate={onInlineCreate}
                  onCancelCreate={onCancelCreate}
                  onRequestCreate={onRequestCreate}
                  onRename={handleChildRename}
                  onMoveInto={onMoveInto}
                />
              ) : (
                <FileItem
                  key={entry.path}
                  threadId={threadId}
                  workspaceId={workspaceId}
                  entry={entry}
                  depth={depth}
                  status={statusByPath.get(norm(entry.path))}
                  onOpenFile={onOpenFile}
                  onAddToContext={onAddToContext}
                  onDelete={onDelete}
                  onRename={handleChildRename}
                />
              ),
            )}
        </div>
      )}
    </div>
  );
}
