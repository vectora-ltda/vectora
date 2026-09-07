export interface PersistedTabState {
  id: string;
  title: string;
  history: string[];
  historyIndex: number;
  iframeKey: number;
  viewId: number | null;
  desktopUrl: string;
  canGoBack: boolean;
  canGoForward: boolean;
}

export interface PersistedBrowserSession {
  tabs: PersistedTabState[];
  activeTabId: string;
}

interface BrowserViewBridge {
  destroyView: (viewId: number) => void;
}

const browserSessions = new Map<string, PersistedBrowserSession>();
const browserSessionGenerations = new Map<string, number>();

function getBrowserViewBridge(): BrowserViewBridge | undefined {
  return typeof window !== "undefined"
    ? window.vectora?.browserView
    : undefined;
}

export function getBrowserSession(
  sessionKey: string,
): PersistedBrowserSession | undefined {
  return browserSessions.get(sessionKey);
}

export function setBrowserSession(
  sessionKey: string,
  session: PersistedBrowserSession,
): void {
  browserSessions.set(sessionKey, session);
}

export function getBrowserSessionGeneration(sessionKey: string): number {
  return browserSessionGenerations.get(sessionKey) ?? 0;
}

/** Destroys native views and forgets one thread's persisted browser session. */
export function disposeBrowserSession(sessionKey: string): void {
  browserSessionGenerations.set(
    sessionKey,
    getBrowserSessionGeneration(sessionKey) + 1,
  );
  const session = browserSessions.get(sessionKey);
  if (!session) return;
  const browserView = getBrowserViewBridge();
  if (browserView) {
    for (const tab of session.tabs) {
      if (tab.viewId !== null) browserView.destroyView(tab.viewId);
    }
  }
  browserSessions.delete(sessionKey);
}

/** Destroys every browser session belonging to a workspace. */
export function disposeBrowserWorkspace(workspaceId: string): void {
  const prefix = `${workspaceId}:`;
  for (const sessionKey of Array.from(browserSessions.keys())) {
    if (sessionKey.startsWith(prefix)) disposeBrowserSession(sessionKey);
  }
}

/** Destroys every cached session for a thread, regardless of its workspace. */
export function disposeBrowserThread(threadId: string): void {
  const suffix = `:${threadId}`;
  for (const sessionKey of Array.from(browserSessions.keys())) {
    if (sessionKey.endsWith(suffix)) disposeBrowserSession(sessionKey);
  }
}

export function clearBrowserSessionCache(): void {
  for (const sessionKey of browserSessions.keys()) {
    browserSessionGenerations.set(
      sessionKey,
      getBrowserSessionGeneration(sessionKey) + 1,
    );
  }
  browserSessions.clear();
}
