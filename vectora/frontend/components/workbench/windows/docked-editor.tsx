"use client";

import { Code2 } from "lucide-react";
import { useWindowsStore } from "@/lib/stores/windows-store";
import { FileEditor } from "@/components/workbench/file-editor";
import { m } from "@/lib/paraglide/messages";
import { EditorTab } from "@/components/workbench/windows/editor-tab";
import { editorKey, useEditorRegistry } from "@/lib/stores/editor-registry";

interface DockedEditorProps {
  /** Workspace ativo da sessão atual. Quando fornecido e divergente de
   * `dockedWorkspaceId`, o editor não renderiza — defesa em profundidade
   * contra estado docked vazado de outro workspace (ex. via localStorage
   * de uma versão anterior sem `partialize`, ou uma race de navegação). */
  activeWorkspaceId?: string | null;
}

export function DockedEditor({ activeWorkspaceId }: DockedEditorProps = {}) {
  const dockedWorkspaceId = useWindowsStore((s) => s.dockedWorkspaceId);
  const dockedTabs = useWindowsStore((s) => s.dockedTabs);
  const dockedActiveTab = useWindowsStore((s) => s.dockedActiveTab);
  const setDockedActiveTab = useWindowsStore((s) => s.setDockedActiveTab);
  const closeDockedTab = useWindowsStore((s) => s.closeDockedTab);
  const entries = useEditorRegistry((s) => s.entries);

  const belongsToOtherWorkspace =
    activeWorkspaceId != null &&
    dockedWorkspaceId != null &&
    dockedWorkspaceId !== activeWorkspaceId;

  if (
    !dockedWorkspaceId ||
    dockedTabs.length === 0 ||
    belongsToOtherWorkspace
  ) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-3 bg-background p-6 text-center">
        <Code2 className="w-8 h-8 text-muted-foreground/40" />
        <p className="text-xs text-muted-foreground">
          {m.docked_editor_empty()}
        </p>
        <p className="text-[11px] text-muted-foreground/60">
          {m.docked_editor_empty_hint()}
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full w-full min-w-0 flex-col bg-background">
      <div className="flex shrink-0 overflow-x-auto border-b border-border/60 bg-sidebar">
        {dockedTabs.map((tab) => {
          const name = tab.split(/[/\\]/).pop() || tab;
          const isActive = tab === dockedActiveTab;
          return (
            <EditorTab
              key={tab}
              name={name}
              path={tab}
              active={isActive}
              dirty={Boolean(entries[editorKey(dockedWorkspaceId, tab)]?.dirty)}
              onActivate={() => setDockedActiveTab(tab)}
              onClose={() => closeDockedTab(tab)}
              onSave={() =>
                entries[editorKey(dockedWorkspaceId, tab)]?.save() ??
                Promise.resolve(false)
              }
              onSaveAs={(target) =>
                entries[editorKey(dockedWorkspaceId, tab)]?.saveAs(target) ??
                Promise.resolve(false)
              }
            />
          );
        })}
      </div>
      <div className="flex-1 min-h-0">
        {dockedActiveTab && (
          <FileEditor workspaceId={dockedWorkspaceId} path={dockedActiveTab} />
        )}
      </div>
    </div>
  );
}
