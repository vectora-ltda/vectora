"use client";

/**
 * FileWindow — janela flutuante (app da workstation) com suporte a múltiplas
 * abas. Arrastável pela barra de título e redimensionável via react-rnd.
 * A aba ativa é renderizada pelo FileEditor (Monaco para texto; viewer para
 * mídia e binários). Fechar a última aba fecha a janela.
 */

import { useState } from "react";
import { Minus, X } from "lucide-react";
import { Rnd } from "react-rnd";

import { FileEditor } from "@/components/workbench/file-editor";
import {
  useWindowsStore,
  type FileWindowState,
} from "@/lib/stores/windows-store";
import { m } from "@/lib/paraglide/messages";
import { EditorTab } from "@/components/workbench/windows/editor-tab";
import { editorKey, useEditorRegistry } from "@/lib/stores/editor-registry";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

const TITLE_BAR_CLASS = "vectora-window-drag-handle";

export function FileWindow({ win }: { win: FileWindowState }) {
  const focus = useWindowsStore((s) => s.focus);
  const close = useWindowsStore((s) => s.close);
  const closeTab = useWindowsStore((s) => s.closeTab);
  const minimize = useWindowsStore((s) => s.minimize);
  const setActiveTab = useWindowsStore((s) => s.setActiveTab);
  const setBounds = useWindowsStore((s) => s.setBounds);
  const entries = useEditorRegistry((s) => s.entries);
  const [closeDialogOpen, setCloseDialogOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const hasTabs = win.tabs.length > 1;
  const dirtyTabs = win.tabs.filter(
    (tab) => entries[editorKey(win.workspaceId, tab)]?.dirty,
  );

  const requestWindowClose = () => {
    if (dirtyTabs.length > 0) setCloseDialogOpen(true);
    else close(win.id);
  };

  const saveAndCloseWindow = async () => {
    setClosing(true);
    const results = await Promise.all(
      dirtyTabs.map((tab) => entries[editorKey(win.workspaceId, tab)]?.save()),
    );
    setClosing(false);
    if (results.every(Boolean)) {
      setCloseDialogOpen(false);
      close(win.id);
    }
  };

  return (
    <Rnd
      size={{ width: win.w, height: win.h }}
      position={{ x: win.x, y: win.y }}
      minWidth={280}
      minHeight={180}
      bounds="window"
      dragHandleClassName={TITLE_BAR_CLASS}
      style={{ zIndex: win.zIndex }}
      onMouseDown={() => focus(win.id)}
      onDragStop={(_e, d) => setBounds(win.id, { x: d.x, y: d.y })}
      onResizeStop={(_e, _dir, ref, _delta, pos) =>
        setBounds(win.id, {
          w: ref.offsetWidth,
          h: ref.offsetHeight,
          x: pos.x,
          y: pos.y,
        })
      }
      className="pointer-events-auto"
    >
      <div className="flex flex-col h-full rounded-lg border border-border bg-sidebar shadow-2xl overflow-hidden">
        {/* Barra de título */}
        <div
          className={`${TITLE_BAR_CLASS} flex items-center gap-2 px-2 h-8 shrink-0 bg-muted/40 border-b border-border/60 cursor-move select-none`}
        >
          <span
            className="flex-1 truncate text-xs font-medium"
            title={win.activeTab}
          >
            {win.title}
          </span>
          <button
            onClick={() => minimize(win.id)}
            className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60"
            aria-label={m.window_minimize()}
            title={m.window_minimize()}
          >
            <Minus className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={requestWindowClose}
            className="p-1 rounded text-muted-foreground hover:text-destructive hover:bg-muted/60"
            aria-label={m.window_close()}
            title={m.window_close()}
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Barra de abas — visível quando há mais de uma aba */}
        {hasTabs && (
          <div className="flex shrink-0 overflow-x-auto border-b border-border/60 bg-muted/20">
            {win.tabs.map((tab) => {
              const isActive = tab === win.activeTab;
              const name = tab.split(/[/\\]/).pop() || tab;
              return (
                <EditorTab
                  key={tab}
                  name={name}
                  path={tab}
                  active={isActive}
                  dirty={Boolean(
                    entries[editorKey(win.workspaceId, tab)]?.dirty,
                  )}
                  onActivate={() => setActiveTab(win.id, tab)}
                  onClose={() => closeTab(win.id, tab)}
                  onSave={() =>
                    entries[editorKey(win.workspaceId, tab)]?.save() ??
                    Promise.resolve(false)
                  }
                  onSaveAs={(target) =>
                    entries[editorKey(win.workspaceId, tab)]?.saveAs(target) ??
                    Promise.resolve(false)
                  }
                />
              );
            })}
          </div>
        )}

        {/* Conteúdo da aba ativa */}
        <div className="flex-1 min-h-0 bg-background">
          <FileEditor workspaceId={win.workspaceId} path={win.activeTab} />
        </div>
      </div>
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
              disabled={closing}
              onClick={() => {
                setCloseDialogOpen(false);
                close(win.id);
              }}
            >
              {m.workbench_files_discard()}
            </Button>
            <Button
              disabled={closing}
              onClick={() => void saveAndCloseWindow()}
            >
              {m.workbench_files_save()}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Rnd>
  );
}
