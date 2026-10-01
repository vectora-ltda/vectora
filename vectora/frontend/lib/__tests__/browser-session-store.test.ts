// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";
import {
  clearBrowserSessionHistory,
  getBrowserSession,
  setBrowserSession,
  type PersistedBrowserSession,
} from "../browser-session-store";

const SESSION_KEY = "workspace:thread";
const SESSION: PersistedBrowserSession = {
  activeTabId: "tab-1",
  profileId: "session-profile",
  tabs: [
    {
      id: "tab-1",
      title: "Example",
      history: ["https://example.com"],
      historyIndex: 0,
      iframeKey: 0,
      viewId: null,
      desktopUrl: "https://example.com",
      canGoBack: false,
      canGoForward: false,
    },
  ],
};

describe("browser-session-store", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("persiste e restaura a sessão de abas no storage do renderer", () => {
    setBrowserSession(SESSION_KEY, SESSION);
    expect(getBrowserSession(SESSION_KEY)).toEqual(SESSION);
    expect(
      window.localStorage.getItem("vectora-browser-session:workspace:thread"),
    ).toContain("tab-1");
  });

  it("limpa o histórico local sem remover o perfil Chromium", () => {
    setBrowserSession(SESSION_KEY, SESSION);
    clearBrowserSessionHistory(SESSION_KEY);
    const current = getBrowserSession(SESSION_KEY);
    expect(current?.profileId).toBe("session-profile");
    expect(current?.tabs[0].history).toEqual([]);
    expect(current?.tabs[0].historyIndex).toBe(-1);
  });
});
