import { AtSign, Pin, PinOff } from "lucide-react";

import { useWorkbenchStore } from "@/lib/stores/workbench-store";
import { useWindowsStore } from "@/lib/stores/windows-store";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { m } from "@/lib/paraglide/messages";

const EMPTY_PINNED: string[] = [];

/** Seção de arquivos fixados ("pin") da sessão, no topo da árvore. */
export function PinnedSection({
  threadId,
  workspaceId,
  onAddToContext,
}: {
  threadId: string;
  workspaceId: string;
  onAddToContext?: (path: string) => void;
}) {
  const pinned = useWorkbenchStore(
    (s) => s.pinnedFiles[threadId] ?? EMPTY_PINNED,
  );
  const togglePinned = useWorkbenchStore((s) => s.togglePinned);
  const openDocked = useWindowsStore((s) => s.openDocked);
  const openCanvasDocument = useWindowsStore((s) => s.openCanvasDocument);
  const uiMode = useSettingsStore((s) => s.uiMode);

  if (pinned.length === 0) return null;

  return (
    <div className="border-b border-border/40 pb-1 mb-1">
      <div className="px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {m.workbench_files_pinned()}
      </div>
      {pinned.map((path) => {
        const name = path.split(/[/\\]/).pop() ?? path;
        return (
          <div
            key={path}
            className="group flex items-center gap-1 px-2 py-0.5 text-xs hover:bg-muted/50 rounded-sm"
          >
            <Pin className="w-3 h-3 shrink-0 text-primary" />
            <button
              onClick={() => {
                if (uiMode === "assistant") {
                  openCanvasDocument({
                    id: `file:${workspaceId}:${path}`,
                    kind: "file",
                    workspaceId,
                    threadId,
                    title: name,
                    path,
                  });
                } else {
                  openDocked(workspaceId, path);
                }
              }}
              className="flex-1 text-left truncate text-foreground/80 hover:text-foreground"
              title={path}
            >
              {name}
            </button>
            <div className="hidden group-hover:flex items-center gap-0.5">
              {onAddToContext && (
                <button
                  onClick={() => onAddToContext(path)}
                  className="p-0.5 rounded text-muted-foreground hover:text-foreground"
                  title={m.workbench_files_add_context()}
                >
                  <AtSign className="w-3 h-3" />
                </button>
              )}
              <button
                onClick={() => togglePinned(threadId, path)}
                className="p-0.5 rounded text-muted-foreground hover:text-foreground"
                aria-label={m.workbench_files_unpin()}
                title={m.workbench_files_unpin()}
              >
                <PinOff className="w-3 h-3" />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
