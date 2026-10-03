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
  /** Stable Chromium partition key for this browser session. */
  profileId?: string;
}

interface BrowserViewBridge {
  destroyView: (viewId: number) => void;
}

const browserSessions = new Map<string, PersistedBrowserSession>();
const browserSessionGenerations = new Map<string, number>();
const STORAGE_PREFIX = "vectora-browser-session:";

/** Namespace persisted state by workspace and thread. */
function storageKey(sessionKey: string): string {
  return `${STORAGE_PREFIX}${sessionKey}`;
}

/** Restore tab history without reusing process-local native view identifiers. */
function readPersistedSession(
  sessionKey: string,
): PersistedBrowserSession | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const raw = window.localStorage.getItem(storageKey(sessionKey));
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as Partial<PersistedBrowserSession>;
    if (!Array.isArray(parsed.tabs) || typeof parsed.activeTabId !== "string") {
      return undefined;
    }
    // Native WebContentsView ids belong to the current Electron process. They
    // must never be restored from localStorage after a restart; the Browser
    // tab will create a fresh view and navigate to the persisted URL.
    return {
      ...parsed,
      tabs: parsed.tabs.map((tab) => ({
        ...tab,
        viewId: null,
        canGoBack: false,
        canGoForward: false,
      })),
    } as PersistedBrowserSession;
  } catch {
    return undefined;
  }
}

/** Derives a collision-free, partition-safe identifier from a session key. */
export function getBrowserProfileId(sessionKey: string): string {
  const bytes = new TextEncoder().encode(sessionKey);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const encoded = btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
  return `session-${encoded}`;
}

/** Resolve the native lifecycle bridge without accessing window during SSR. */
function getBrowserViewBridge(): BrowserViewBridge | undefined {
  return typeof window !== "undefined"
    ? window.vectora?.browserView
    : undefined;
}

/** Prefer the live cache, hydrating from local storage only when needed. */
export function getBrowserSession(
  sessionKey: string,
): PersistedBrowserSession | undefined {
  const cached = browserSessions.get(sessionKey);
  if (cached) return cached;
  const persisted = readPersistedSession(sessionKey);
  if (persisted) browserSessions.set(sessionKey, persisted);
  return persisted;
}

/** Persist tab state while retaining the in-memory copy if storage fails. */
export function setBrowserSession(
  sessionKey: string,
  session: PersistedBrowserSession,
): void {
  browserSessions.set(sessionKey, session);
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(
        storageKey(sessionKey),
        JSON.stringify(session),
      );
    } catch {
      // Storage may be unavailable or full; the in-memory session remains valid.
    }
  }
}

/** Removes renderer-owned tab history while keeping the Chromium profile. */
export function clearBrowserSessionHistory(sessionKey: string): void {
  const session = getBrowserSession(sessionKey);
  if (!session) return;
  const cleared: PersistedBrowserSession = {
    ...session,
    tabs: session.tabs.map((tab) => ({
      ...tab,
      history: [],
      historyIndex: -1,
      iframeKey: tab.iframeKey + 1,
      canGoBack: false,
      canGoForward: false,
    })),
  };
  setBrowserSession(sessionKey, cleared);
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("vectora:browser-clear-history", {
        detail: { sessionKey },
      }),
    );
  }
}

/** Read the disposal generation used to reject stale asynchronous view creation. */
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
  if (typeof window !== "undefined") {
    try {
      window.localStorage.removeItem(storageKey(sessionKey));
    } catch {
      // Ignore unavailable storage during teardown.
    }
  }
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
