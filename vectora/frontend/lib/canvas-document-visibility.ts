import type { CanvasDocumentDescriptor } from "@/lib/stores/windows-store";

/** Returns whether a shared-canvas document belongs to the active context. */
export function isCanvasDocumentVisible(
  document: CanvasDocumentDescriptor,
  workspaceId: string | null,
  threadId: string,
): boolean {
  if (!threadId.trim() || workspaceId === "" || document.workspaceId === "") {
    return false;
  }
  if (document.workspaceId !== workspaceId) return false;
  if (document.kind === "file") return workspaceId !== null;
  return document.threadId === threadId;
}
