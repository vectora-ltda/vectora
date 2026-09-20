import type { CanvasDocumentDescriptor } from "@/lib/stores/windows-store";

/** Returns whether a shared-canvas document belongs to the active context. */
export function isCanvasDocumentVisible(
  document: CanvasDocumentDescriptor,
  workspaceId: string | null,
  threadId: string,
): boolean {
  return (
    document.workspaceId === workspaceId &&
    (document.kind === "file" || document.threadId === threadId)
  );
}
