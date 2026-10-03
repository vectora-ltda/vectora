import {
  getBrowserProfileId,
  getBrowserSession,
} from "@/lib/browser-session-store";

/** Resolves the persisted profile before falling back to the session identity. */
export function resolveBrowserProfileId(
  threadId: string | null,
  workspaceId: string | null,
): string | null {
  if (!threadId) return null;
  const sessionKey = `${workspaceId ?? ""}:${threadId}`;
  return (
    getBrowserSession(sessionKey)?.profileId ?? getBrowserProfileId(sessionKey)
  );
}

export { getBrowserProfileId };
